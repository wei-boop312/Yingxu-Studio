-- A reviewed local fallback is not a supplier receipt, success or billing proof.
CREATE TABLE ops.detection_manual_deliveries (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, product_id uuid NOT NULL,
 task_id uuid NOT NULL UNIQUE, attempt_id uuid NOT NULL UNIQUE, final_version_id uuid NOT NULL,
 final_sha256 text NOT NULL CHECK(final_sha256 ~ '^[0-9a-f]{64}$'),
 facts jsonb NOT NULL CHECK(jsonb_typeof(facts)='object' AND (facts->'analysis_complete') IS NOT DISTINCT FROM 'false'::jsonb),
 actor text NOT NULL, reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 4000),
 evidence_digest text NOT NULL CHECK(evidence_digest ~ '^[0-9a-f]{64}$'),
 operation_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id),
 FOREIGN KEY(user_id,task_id,attempt_id) REFERENCES app.provider_attempts(user_id,task_id,id),
 FOREIGN KEY(user_id,product_id,final_version_id) REFERENCES app.asset_versions(user_id,product_id,id)
);
ALTER TABLE ops.detection_manual_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.detection_manual_deliveries FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.detection_manual_deliveries FROM PUBLIC;
GRANT SELECT,INSERT ON ops.detection_manual_deliveries TO app_executor;
GRANT SELECT ON ops.detection_manual_deliveries TO ops_admin;
CREATE POLICY detection_delivery_executor ON ops.detection_manual_deliveries TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY detection_delivery_operator ON ops.detection_manual_deliveries FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_detection_delivery BEFORE UPDATE OR DELETE ON ops.detection_manual_deliveries FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION ops.approve_detection_manual_delivery(p_task uuid,p_attempt uuid,p_final_sha text,p_operation uuid,p_reason text,p_evidence text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; v app.asset_versions; prior ops.audit_events; existing_decision ops.detection_manual_deliveries; identity uuid; fallback jsonb; next_phase text;
BEGIN
 IF p_task IS NULL OR p_attempt IS NULL OR p_operation IS NULL OR p_final_sha IS NULL OR p_final_sha !~ '^[0-9a-f]{64}$'
 OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_evidence IS NULL OR p_evidence !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_detection_delivery_review'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action IS DISTINCT FROM 'detection_manual_delivery' OR prior.target_id IS DISTINCT FROM p_task
  OR prior.reason IS DISTINCT FROM p_reason OR prior.evidence_digest IS DISTINCT FROM p_evidence
  OR prior.after_state->>'attempt_id' IS DISTINCT FROM p_attempt::text
  OR prior.after_state->>'final_sha256' IS DISTINCT FROM p_final_sha THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN (prior.after_state->>'decision_id')::uuid;
 END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status<>'uncertain' OR t.credits_status<>'frozen' OR t.final_version_id IS NULL
 THEN RAISE EXCEPTION 'detection_delivery_requires_unresolved_final'; END IF;
 SELECT * INTO existing_decision FROM ops.detection_manual_deliveries WHERE task_id=t.id;
 IF (existing_decision.id IS NULL AND (t.phase<>'detecting' OR t.compliance_id IS NOT NULL))
 OR (existing_decision.id IS NOT NULL AND t.phase NOT IN ('detecting','validating_rules','finalizing'))
 THEN RAISE EXCEPTION 'detection_delivery_requires_unresolved_final'; END IF;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u FOR UPDATE;
 IF NOT FOUND OR a.step<>'detect' OR a.status NOT IN ('intent_recorded','submitted','polling','uncertain','failed') THEN RAISE EXCEPTION 'unresolved_detection_required'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND user_id=u AND step='generate' AND status='succeeded' AND provider_id IS NOT NULL)
 THEN RAISE EXCEPTION 'known_generation_required'; END IF;
 SELECT av.* INTO v FROM app.asset_versions av JOIN app.assets asset ON asset.id=av.asset_id AND asset.user_id=av.user_id
 WHERE av.id=t.final_version_id AND av.user_id=u AND av.product_id=t.product_id AND av.canonical AND asset.status='ready' AND asset.kind='final';
 IF NOT FOUND OR v.sha256 IS DISTINCT FROM p_final_sha THEN RAISE EXCEPTION 'final_hash_mismatch'; END IF;
 next_phase:='validating_rules';
 IF existing_decision.id IS NOT NULL THEN
  IF existing_decision.attempt_id IS DISTINCT FROM a.id OR existing_decision.final_version_id IS DISTINCT FROM v.id OR existing_decision.final_sha256 IS DISTINCT FROM v.sha256
  THEN RAISE EXCEPTION 'manual_detection_scope_mismatch'; END IF;
  IF t.compliance_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM app.compliance_checks WHERE id=t.compliance_id AND task_id=t.id AND final_version_id=v.id AND final_sha256=v.sha256 AND facts=existing_decision.facts AND auto_overall<>'compliant')
   THEN RAISE EXCEPTION 'invalid_manual_detection_report'; END IF;
   next_phase:='finalizing';
  END IF;
  identity:=existing_decision.id;
 ELSE
  fallback:=jsonb_build_object('schema_version',1,'analysis_complete',false,'image_width',v.width,'image_height',v.height,
  'text_items','[]'::jsonb,'logo_items','[]'::jsonb,'watermark_items','[]'::jsonb,
  'background',jsonb_build_object('classification','unknown','confidence',0.0),'product_extent',NULL,
  'uncertainties',jsonb_build_array('detection_unavailable_operator_delivery'));
  INSERT INTO ops.detection_manual_deliveries(user_id,product_id,task_id,attempt_id,final_version_id,final_sha256,facts,actor,reason,evidence_digest,operation_id)
  VALUES(u,t.product_id,t.id,a.id,v.id,v.sha256,fallback,session_user,p_reason,p_evidence,p_operation) RETURNING id INTO identity;
 END IF;
 UPDATE app.generation_tasks SET status='processing',phase=next_phase,lease_owner=NULL,lease_until=now(),
 fencing_token=fencing_token+1,revision=revision+1,updated_at=now(),error_code='manual_detection_delivery_approved' WHERE id=t.id;
 UPDATE ops.task_dispatch SET state='processing',lease_until=now(),next_recovery_at=now(),recovery_attempts=0,fencing_token=t.fencing_token+1 WHERE task_id=t.id;
 INSERT INTO ops.outbox(user_id,task_id,event_type,dedupe_key) VALUES(u,t.id,'task.recovery',t.id::text||'-manual-detection-'||p_operation::text);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'detection_manual_delivery','task',t.id,p_reason,p_evidence,
 jsonb_build_object('status',t.status,'phase',t.phase,'credits_status',t.credits_status),
 jsonb_build_object('status','processing','phase',next_phase,'decision_id',identity,'attempt_id',a.id,'final_sha256',v.sha256,'supplier_outcome',a.status,'supplier_cost_verified',false,'resumed_existing_decision',existing_decision.id IS NOT NULL),p_operation);
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','processing','phase',next_phase,'revision',t.revision+1));
 RETURN identity;
END $$;

CREATE FUNCTION ops.detection_delivery_context(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 RETURN jsonb_build_object(
 'task',(SELECT jsonb_build_object('id',id,'status',status,'phase',phase,'credits_status',credits_status,'error_code',error_code) FROM app.generation_tasks WHERE id=p_task),
 'final',(SELECT jsonb_build_object('version_id',v.id,'sha256',v.sha256,'width',v.width,'height',v.height,'canonical',v.canonical,'ready',a.status='ready') FROM app.asset_versions v JOIN app.assets a ON a.id=v.asset_id AND a.user_id=v.user_id JOIN app.generation_tasks t ON t.final_version_id=v.id AND t.user_id=v.user_id WHERE t.id=p_task),
 'detect_attempt',(SELECT jsonb_build_object('id',id,'step',step,'provider',provider,'model',model,'status',status,'provider_id',provider_id) FROM app.provider_attempts WHERE task_id=p_task AND step='detect'),
 'decision',(SELECT jsonb_build_object('id',id,'attempt_id',attempt_id,'final_sha256',final_sha256,'actor',actor,'reason',reason,'evidence_digest',evidence_digest,'original_operation_id',operation_id,'created_at',created_at) FROM ops.detection_manual_deliveries WHERE task_id=p_task),
 'latest_review',(SELECT jsonb_build_object('operation_id',request_id,'actor',actor,'reason',reason,'evidence_digest',evidence_digest,'created_at',created_at) FROM ops.audit_events WHERE target_id=p_task AND action='detection_manual_delivery' ORDER BY created_at DESC,id DESC LIMIT 1),
 'supplier_cost_verified',false);
END $$;
ALTER FUNCTION ops.detection_delivery_context(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.detection_delivery_context(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.detection_delivery_context(uuid) TO ops_admin;

-- Public report metadata derives from the immutable operator decision, not from
-- arbitrary text produced by the vision model. Tenant RLS stays in force.
CREATE FUNCTION app.is_manual_detection_delivery(p_check uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.compliance_checks c JOIN ops.detection_manual_deliveries d
 ON d.user_id=c.user_id AND d.task_id=c.task_id AND d.final_version_id=c.final_version_id
 AND d.final_sha256=c.final_sha256 AND d.facts=c.facts WHERE c.id=p_check)
$$;
ALTER FUNCTION app.is_manual_detection_delivery(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.is_manual_detection_delivery(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.is_manual_detection_delivery(uuid) TO app_api;
ALTER FUNCTION ops.approve_detection_manual_delivery(uuid,uuid,text,uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.approve_detection_manual_delivery(uuid,uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.approve_detection_manual_delivery(uuid,uuid,text,uuid,text,text) TO ops_admin;

-- Retain the 0015 manual-queue, lease and unresolved-generation barriers.
-- An immutable reviewed DETECT fallback may resume only local rule/report phases.
CREATE OR REPLACE FUNCTION ops.claim_task(p_task uuid,p_owner text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_owner'; END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task AND state<>'manual';
 IF u IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status IN ('succeeded','failed','cancelled','uncertain') OR (t.lease_until IS NOT NULL AND t.lease_until>clock_timestamp()) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts a WHERE a.task_id=t.id AND a.provider_id IS NULL AND a.status IN ('intent_recorded','uncertain')
 AND NOT(a.step='detect' AND t.phase IN ('validating_rules','finalizing')
 AND EXISTS(SELECT 1 FROM ops.detection_manual_deliveries d JOIN app.asset_versions v ON v.id=d.final_version_id AND v.user_id=d.user_id
 WHERE d.task_id=t.id AND d.user_id=t.user_id AND d.attempt_id=a.id AND d.final_version_id=t.final_version_id AND d.final_sha256=v.sha256))) THEN
  UPDATE app.generation_tasks SET status='uncertain',lease_owner=NULL,lease_until=NULL,revision=revision+1,updated_at=now(),error_code='submission_outcome_unknown' WHERE id=t.id;
  UPDATE ops.task_dispatch SET state='uncertain',lease_until=NULL WHERE task_id=t.id;
  INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','uncertain','revision',t.revision+1));
  RETURN jsonb_build_object('id',t.id,'status','uncertain');
 END IF;
 UPDATE app.generation_tasks SET status='processing',phase=CASE WHEN status='queued' THEN 'submitting' ELSE phase END,
  lease_owner=p_owner,lease_until=clock_timestamp()+interval '60 seconds',fencing_token=fencing_token+1,revision=revision+1,updated_at=now()
 WHERE id=t.id RETURNING * INTO t;
 UPDATE ops.task_dispatch SET state='processing',lease_until=t.lease_until,fencing_token=t.fencing_token WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status',t.status,'phase',t.phase,'revision',t.revision));
 RETURN jsonb_build_object('id',t.id,'status',t.status,'phase',t.phase,'fencing_token',t.fencing_token);
END $$;

CREATE OR REPLACE FUNCTION app.worker_context(p_task uuid,p_owner text,p_fence bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; source app.asset_versions;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO source FROM app.asset_versions WHERE id=t.source_asset_version_id AND user_id=t.user_id;
 RETURN to_jsonb(t)||jsonb_build_object('source',to_jsonb(source),'generated',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=t.generated_version_id),'final',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=t.final_version_id),
 'detection_degradation',(SELECT to_jsonb(d) FROM ops.detection_manual_deliveries d WHERE d.task_id=t.id AND d.user_id=t.user_id),
 'cost_observations',COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt_id',c.attempt_id,'kind',c.cost_record_kind)) FROM app.task_cost_records c WHERE c.task_id=t.id),'[]'::jsonb),'attempts',COALESCE((SELECT jsonb_agg(to_jsonb(a)) FROM app.provider_attempts a WHERE task_id=t.id),'[]'::jsonb));
END $$;

-- Keep the 0021 rule-result guard and the same worker function OID/ACL.
CREATE OR REPLACE FUNCTION app.record_report(p_task uuid,p_owner text,p_fence bigint,p_id uuid,p_final_sha text,p_facts jsonb,p_results jsonb,p_overall text,p_sampling boolean,p_report_sha text,p_bucket text,p_key text,p_storage_version text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; v app.asset_versions; d ops.detection_manual_deliveries;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF t.compliance_id IS NOT NULL THEN RETURN t.compliance_id; END IF;
 IF t.phase NOT IN ('detecting','validating_rules') THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 SELECT * INTO v FROM app.asset_versions WHERE id=t.final_version_id;
 IF NOT FOUND OR v.sha256 IS DISTINCT FROM p_final_sha THEN RAISE EXCEPTION 'final_hash_mismatch'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='detect') THEN RAISE EXCEPTION 'detection_intent_required'; END IF;
 SELECT * INTO d FROM ops.detection_manual_deliveries WHERE task_id=t.id AND user_id=t.user_id;
 IF FOUND THEN
  IF t.phase<>'validating_rules' OR d.final_version_id IS DISTINCT FROM v.id OR d.final_sha256 IS DISTINCT FROM p_final_sha
  OR d.facts IS DISTINCT FROM p_facts OR p_overall='compliant' THEN RAISE EXCEPTION 'invalid_manual_detection_report'; END IF;
 ELSE
  IF t.phase<>'detecting' THEN RAISE EXCEPTION 'manual_detection_review_required'; END IF;
  IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='detect' AND provider_id IS NOT NULL AND response_digest IS NOT NULL AND status IN ('submitted','polling','succeeded'))
  THEN RAISE EXCEPTION 'detection_receipt_required'; END IF;
 END IF;
 IF p_overall='compliant' AND NOT app.rule_result_can_pass(p_facts,t.input_snapshot->'_execution'->'rules',p_results,p_sampling) THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,t.user_id,t.product_id,t.id,v.id,v.sha256,p_facts,1,t.input_snapshot->'_execution'->'rules',p_results,p_overall,p_sampling,p_report_sha,p_bucket,p_key,p_storage_version);
 IF d.id IS NULL THEN
  UPDATE app.provider_attempts SET status='succeeded',finished_at=now() WHERE task_id=t.id AND step='detect';
 END IF;
 UPDATE app.generation_tasks SET compliance_id=p_id,phase='finalizing',revision=revision+1,updated_at=now() WHERE id=t.id;
 RETURN p_id;
END $$;
