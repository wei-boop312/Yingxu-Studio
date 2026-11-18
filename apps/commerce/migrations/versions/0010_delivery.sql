ALTER TABLE app.generation_tasks ADD COLUMN generated_version_id uuid;
ALTER TABLE app.generation_tasks ADD CONSTRAINT generated_version_owned FOREIGN KEY(user_id,product_id,generated_version_id) REFERENCES app.asset_versions(user_id,product_id,id);
CREATE FUNCTION app.record_task_asset(p_task uuid,p_owner text,p_fence bigint,p_kind text,p_asset uuid,p_version uuid,p_bucket text,p_key text,p_storage_version text,p_sha text,p_width integer,p_height integer,p_bytes bigint) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_kind NOT IN ('generated','final') THEN RAISE EXCEPTION 'invalid_asset_kind'; END IF;
 IF p_kind='generated' AND t.generated_version_id IS NOT NULL THEN RETURN t.generated_version_id; END IF;
 IF p_kind='final' AND t.final_version_id IS NOT NULL THEN RETURN t.final_version_id; END IF;
 IF (p_kind='generated' AND t.phase NOT IN ('polling','archiving')) OR (p_kind='final' AND t.phase<>'protecting') THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 IF p_width IS DISTINCT FROM (t.input_snapshot->'generation'->'output'->>'width')::integer
 OR p_height IS DISTINCT FROM (t.input_snapshot->'generation'->'output'->>'height')::integer THEN RAISE EXCEPTION 'output_size_mismatch'; END IF;
 IF p_kind='generated' AND NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='generate' AND provider_id IS NOT NULL AND status IN ('submitted','polling','succeeded')) THEN RAISE EXCEPTION 'generation_receipt_required'; END IF;
 INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(p_asset,t.user_id,t.product_id,p_kind,'uploading');
 INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,storage_version_id,sha256,mime,width,height,byte_size,color_space,canonical,source_version_id)
 VALUES(p_version,t.user_id,t.product_id,p_asset,1,p_bucket,p_key,p_storage_version,p_sha,'image/png',p_width,p_height,p_bytes,'sRGB',true,CASE WHEN p_kind='final' THEN t.generated_version_id ELSE t.source_asset_version_id END);
 UPDATE app.assets SET status='ready',current_version_id=p_version WHERE id=p_asset;
 IF p_kind='generated' THEN
  UPDATE app.provider_attempts SET status='succeeded',finished_at=now() WHERE task_id=t.id AND step='generate';
  UPDATE app.generation_tasks SET generated_version_id=p_version,phase='protecting',revision=revision+1,updated_at=now() WHERE id=t.id;
 ELSE
  UPDATE app.generation_tasks SET final_version_id=p_version,phase='detecting',revision=revision+1,updated_at=now() WHERE id=t.id;
 END IF;
 RETURN p_version;
END $$;
CREATE FUNCTION app.record_report(p_task uuid,p_owner text,p_fence bigint,p_id uuid,p_final_sha text,p_facts jsonb,p_results jsonb,p_overall text,p_sampling boolean,p_report_sha text,p_bucket text,p_key text,p_storage_version text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; v app.asset_versions;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF t.compliance_id IS NOT NULL THEN RETURN t.compliance_id; END IF;
 IF t.phase<>'detecting' THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 SELECT * INTO v FROM app.asset_versions WHERE id=t.final_version_id;
 IF NOT FOUND OR v.sha256 IS DISTINCT FROM p_final_sha THEN RAISE EXCEPTION 'final_hash_mismatch'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='detect') THEN RAISE EXCEPTION 'detection_intent_required'; END IF;
 IF p_overall='compliant' AND (COALESCE((p_facts->>'analysis_complete')::boolean,false)=false
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(t.input_snapshot->'_execution'->'rules') AS rule WHERE rule->>'evidence_status' IS DISTINCT FROM 'verified')) THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,t.user_id,t.product_id,t.id,v.id,v.sha256,p_facts,1,t.input_snapshot->'_execution'->'rules',p_results,p_overall,p_sampling,p_report_sha,p_bucket,p_key,p_storage_version);
 UPDATE app.provider_attempts SET status='succeeded',finished_at=now() WHERE task_id=t.id AND step='detect';
 UPDATE app.generation_tasks SET compliance_id=p_id,phase='finalizing',revision=revision+1,updated_at=now() WHERE id=t.id;
 RETURN p_id;
END $$;
CREATE FUNCTION app.settle_task(p_task uuid,p_owner text,p_fence bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; w app.wallets;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO w FROM app.wallets WHERE user_id=u FOR UPDATE;
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status='succeeded' THEN RETURN; END IF;
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF t.phase<>'finalizing' OR NOT EXISTS(
  SELECT 1 FROM app.compliance_checks c JOIN app.asset_versions v ON v.id=c.final_version_id AND v.user_id=c.user_id
  JOIN app.assets a ON a.id=v.asset_id AND a.user_id=v.user_id
  WHERE c.id=t.compliance_id AND c.task_id=t.id AND v.id=t.final_version_id AND v.sha256=c.final_sha256
  AND v.canonical AND a.status='ready' AND c.report_object_key<>'' AND c.report_bucket<>'') THEN RAISE EXCEPTION 'delivery_incomplete'; END IF;
 UPDATE app.wallets SET reserved=reserved-t.reserved_credits,spent_total=spent_total+t.reserved_credits,revision=revision+1,updated_at=now() WHERE user_id=u RETURNING * INTO w;
 INSERT INTO app.credit_transactions(user_id,task_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
 VALUES(u,t.id,gen_random_uuid(),'charge',t.reserved_credits,0,-t.reserved_credits,0,t.reserved_credits,w.available,w.reserved,'system','delivered');
 UPDATE app.generation_tasks SET status='succeeded',credits_status='charged',completed_at=now(),lease_owner=NULL,lease_until=NULL,revision=revision+1,updated_at=now() WHERE id=t.id;
 UPDATE ops.task_dispatch SET state='succeeded',lease_until=NULL WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','succeeded','revision',t.revision+1));
END $$;
CREATE FUNCTION app.fail_task(p_task uuid,p_owner text,p_fence bigint,p_code text,p_uncertain boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; w app.wallets;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO w FROM app.wallets WHERE user_id=u FOR UPDATE;
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status='failed' AND NOT p_uncertain THEN RETURN; END IF;
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_uncertain THEN
  UPDATE app.generation_tasks SET status='uncertain',lease_owner=NULL,lease_until=NULL,error_code=p_code,revision=revision+1,updated_at=now() WHERE id=t.id;
  UPDATE app.provider_attempts SET status='uncertain' WHERE task_id=t.id AND status NOT IN ('succeeded','failed');
 ELSE
  IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND status IN ('intent_recorded','submitted','polling','uncertain')) THEN RAISE EXCEPTION 'unresolved_attempt_requires_reconciliation'; END IF;
  UPDATE app.wallets SET available=available+t.reserved_credits,reserved=reserved-t.reserved_credits,revision=revision+1,updated_at=now() WHERE user_id=u RETURNING * INTO w;
  INSERT INTO app.credit_transactions(user_id,task_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
  VALUES(u,t.id,gen_random_uuid(),'refund',t.reserved_credits,t.reserved_credits,-t.reserved_credits,0,0,w.available,w.reserved,'system',p_code);
  UPDATE app.generation_tasks SET status='failed',credits_status='refunded',completed_at=now(),lease_owner=NULL,lease_until=NULL,error_code=p_code,revision=revision+1,updated_at=now() WHERE id=t.id;
 END IF;
 UPDATE ops.task_dispatch SET state=CASE WHEN p_uncertain THEN 'uncertain' ELSE 'failed' END,lease_until=NULL WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status',CASE WHEN p_uncertain THEN 'uncertain' ELSE 'failed' END,'revision',t.revision+1));
END $$;
ALTER FUNCTION app.record_task_asset(uuid,text,bigint,text,uuid,uuid,text,text,text,text,integer,integer,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_task_asset(uuid,text,bigint,text,uuid,uuid,text,text,text,text,integer,integer,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_task_asset(uuid,text,bigint,text,uuid,uuid,text,text,text,text,integer,integer,bigint) TO app_worker;
ALTER FUNCTION app.record_report(uuid,text,bigint,uuid,text,jsonb,jsonb,text,boolean,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_report(uuid,text,bigint,uuid,text,jsonb,jsonb,text,boolean,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_report(uuid,text,bigint,uuid,text,jsonb,jsonb,text,boolean,text,text,text,text) TO app_worker;
ALTER FUNCTION app.settle_task(uuid,text,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.settle_task(uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.settle_task(uuid,text,bigint) TO app_worker;
ALTER FUNCTION app.fail_task(uuid,text,bigint,text,boolean) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.fail_task(uuid,text,bigint,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.fail_task(uuid,text,bigint,text,boolean) TO app_worker;
