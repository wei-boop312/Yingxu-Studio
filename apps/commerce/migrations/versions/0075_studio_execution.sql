CREATE TABLE app.studio_execution_intents (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, project_id uuid NOT NULL,
 slot_id uuid NOT NULL, plan_id uuid NOT NULL, source_asset_version_id uuid NOT NULL,
 target_url text NOT NULL, prompt_id uuid NOT NULL UNIQUE,
 execution_kind text NOT NULL CHECK(execution_kind='connection_test'),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','preparing','submitting','submitted','uncertain','succeeded','failed','cancelled')),
 request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'), idempotency_key text NOT NULL,
 snapshot jsonb NOT NULL DEFAULT '{}', result jsonb NOT NULL DEFAULT '{}',
 cancel_requested boolean NOT NULL DEFAULT false, submission_started_at timestamptz,
 lease_owner text, lease_until timestamptz, fencing_token bigint NOT NULL DEFAULT 0,
 next_poll_at timestamptz NOT NULL DEFAULT now(),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), UNIQUE(user_id,project_id,slot_id,id), UNIQUE(user_id,idempotency_key),
 FOREIGN KEY(user_id,product_id,project_id) REFERENCES app.studio_projects(user_id,product_id,id),
 FOREIGN KEY(user_id,project_id,slot_id,plan_id) REFERENCES app.studio_plans(user_id,project_id,slot_id,id),
 FOREIGN KEY(user_id,product_id,source_asset_version_id) REFERENCES app.asset_versions(user_id,product_id,id)
);
ALTER TABLE app.studio_execution_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.studio_execution_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.studio_execution_intents TO app_api,app_readonly
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY executor_coordinator ON app.studio_execution_intents TO app_executor USING(true) WITH CHECK(true);
GRANT SELECT,INSERT ON app.studio_execution_intents TO app_api;
GRANT SELECT ON app.studio_execution_intents TO app_readonly;
GRANT SELECT,UPDATE ON app.studio_execution_intents TO app_executor;
CREATE INDEX studio_intent_scan ON app.studio_execution_intents(next_poll_at,created_at)
 WHERE status NOT IN ('succeeded','failed','cancelled');
CREATE INDEX studio_intent_plan ON app.studio_execution_intents(user_id,project_id,slot_id,plan_id);

CREATE TABLE app.studio_execution_receipts (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, intent_id uuid NOT NULL, kind text NOT NULL,
 bucket text NOT NULL, object_key text NOT NULL, storage_version_id text, sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(intent_id,kind,sha256),
 FOREIGN KEY(user_id,intent_id) REFERENCES app.studio_execution_intents(user_id,id)
);
ALTER TABLE app.studio_execution_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.studio_execution_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.studio_execution_receipts USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
GRANT SELECT ON app.studio_execution_receipts TO app_api,app_readonly;
GRANT SELECT,INSERT ON app.studio_execution_receipts TO app_executor;
CREATE TRIGGER immutable_studio BEFORE UPDATE OR DELETE ON app.studio_execution_receipts FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.studio_outputs ADD COLUMN execution_intent_id uuid;
ALTER TABLE app.studio_outputs ADD CONSTRAINT studio_output_execution_scope
 FOREIGN KEY(user_id,project_id,slot_id,execution_intent_id) REFERENCES app.studio_execution_intents(user_id,project_id,slot_id,id);
ALTER TABLE app.studio_outputs DROP CONSTRAINT studio_outputs_check;
ALTER TABLE app.studio_outputs ADD CONSTRAINT studio_output_comfy_intent
 CHECK(route<>'comfyui' OR task_id IS NOT NULL OR execution_intent_id IS NOT NULL);
CREATE UNIQUE INDEX studio_output_once_per_intent ON app.studio_outputs(execution_intent_id) WHERE execution_intent_id IS NOT NULL;
GRANT SELECT ON app.studio_skus,app.studio_slots,app.studio_plans,app.studio_plan_facts,app.studio_fact_versions TO app_executor;
GRANT INSERT ON app.studio_outputs TO app_executor;
GRANT UPDATE ON app.studio_slots,app.studio_projects TO app_executor;

CREATE FUNCTION ops.claim_studio_intent(p_id uuid,p_owner text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.studio_execution_intents;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_owner'; END IF;
 SELECT * INTO j FROM app.studio_execution_intents WHERE (p_id IS NULL OR id=p_id)
 AND status NOT IN ('succeeded','failed','cancelled') AND next_poll_at<=now()
 AND (lease_until IS NULL OR lease_until<now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF j.status='submitting' THEN j.status:='uncertain'; END IF;
 IF j.status='queued' THEN j.status:='preparing'; END IF;
 UPDATE app.studio_execution_intents SET status=j.status,lease_owner=p_owner,lease_until=now()+interval '120 seconds',
 fencing_token=fencing_token+1,updated_at=now() WHERE id=j.id RETURNING * INTO j;
 PERFORM set_config('app.user_id',j.user_id::text,true);
 RETURN to_jsonb(j)||jsonb_build_object('basis_current',
 EXISTS(SELECT 1 FROM app.studio_slots s WHERE s.id=j.slot_id AND NOT s.archived
 AND j.plan_id=(SELECT id FROM app.studio_plans WHERE slot_id=j.slot_id ORDER BY revision DESC LIMIT 1))
 AND NOT EXISTS(SELECT 1 FROM app.studio_plan_facts pf JOIN app.studio_fact_versions f ON f.id=pf.fact_version_id
 WHERE pf.plan_id=j.plan_id AND EXISTS(SELECT 1 FROM app.studio_fact_versions n WHERE n.sku_id=f.sku_id AND n.fact_key=f.fact_key AND n.revision>f.revision)),
 'source',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=j.source_asset_version_id),
 'plan',(SELECT snapshot FROM app.studio_plans WHERE id=j.plan_id));
END $$;

CREATE FUNCTION ops.advance_studio_intent(p_id uuid,p_owner text,p_fence bigint,p_action text,p_payload jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.studio_execution_intents;
BEGIN
 SELECT * INTO j FROM app.studio_execution_intents WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR p_fence IS NULL OR p_owner IS NULL OR j.lease_until IS NULL OR j.fencing_token<>p_fence OR j.lease_owner IS DISTINCT FROM p_owner
 OR j.lease_until<now() OR j.status IN ('succeeded','failed','cancelled') THEN
 RAISE EXCEPTION 'stale_studio_lease' USING ERRCODE='55000'; END IF;
 IF p_action='freeze' THEN
  IF j.status<>'preparing' OR j.submission_started_at IS NOT NULL OR j.cancel_requested THEN RAISE EXCEPTION 'invalid_studio_state'; END IF;
  IF p_payload->>'workflow_sha256' IS NULL OR p_payload->>'input_sha256' IS NULL THEN RAISE EXCEPTION 'incomplete_studio_snapshot'; END IF;
  UPDATE app.studio_execution_intents SET snapshot=p_payload WHERE id=p_id;
 ELSIF p_action='start_submission' THEN
  IF j.status<>'preparing' OR j.submission_started_at IS NOT NULL OR j.cancel_requested OR j.snapshot='{}'::jsonb THEN RAISE EXCEPTION 'invalid_studio_state'; END IF;
  UPDATE app.studio_execution_intents SET status='submitting',submission_started_at=now() WHERE id=p_id;
 ELSIF p_action='submitted' THEN
  IF j.status NOT IN ('submitting','uncertain') OR j.submission_started_at IS NULL THEN RAISE EXCEPTION 'invalid_studio_state'; END IF;
  UPDATE app.studio_execution_intents SET status='submitted',result=p_payload,next_poll_at=now(),lease_owner=NULL,lease_until=NULL WHERE id=p_id;
 ELSIF p_action IN ('pending','uncertain') THEN
  IF j.submission_started_at IS NULL THEN RAISE EXCEPTION 'submission_marker_required'; END IF;
  UPDATE app.studio_execution_intents SET status=CASE WHEN p_action='uncertain' THEN 'uncertain' ELSE j.status END,
  result=p_payload,next_poll_at=now()+interval '3 seconds',lease_owner=NULL,lease_until=NULL WHERE id=p_id;
 ELSIF p_action IN ('failed','cancelled') THEN
  IF j.submission_started_at IS NOT NULL AND COALESCE((p_payload->>'definitive')::boolean,false) IS NOT TRUE THEN RAISE EXCEPTION 'unknown_submission_cannot_close'; END IF;
  UPDATE app.studio_execution_intents SET status=p_action,result=p_payload,lease_owner=NULL,lease_until=NULL WHERE id=p_id;
 ELSE RAISE EXCEPTION 'unknown_studio_action'; END IF;
 UPDATE app.studio_execution_intents SET updated_at=now() WHERE id=p_id;
END $$;

CREATE FUNCTION app.cancel_studio_intent(p_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.studio_execution_intents; u uuid;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 SELECT * INTO j FROM app.studio_execution_intents WHERE id=p_id AND user_id=u FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'studio_intent_not_found'; END IF;
 IF j.status NOT IN ('succeeded','failed','cancelled') THEN
  UPDATE app.studio_execution_intents SET cancel_requested=true,
  status=CASE WHEN submission_started_at IS NULL THEN 'cancelled' ELSE status END,updated_at=now()
  WHERE id=p_id RETURNING * INTO j;
 END IF;
 RETURN jsonb_build_object('id',j.id,'status',j.status,'cancel_requested',j.cancel_requested);
END $$;

CREATE FUNCTION ops.append_studio_receipt(p_id uuid,p_kind text,p_bucket text,p_key text,p_version text,p_hash text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.studio_execution_intents;
BEGIN
 SELECT * INTO j FROM app.studio_execution_intents WHERE id=p_id;
 IF NOT FOUND OR p_kind NOT IN ('submission','history','environment','output') OR p_key NOT LIKE 'studio-execution/'||j.user_id::text||'/'||p_id::text||'/%' THEN RAISE EXCEPTION 'invalid_studio_receipt'; END IF;
 PERFORM set_config('app.user_id',j.user_id::text,true);
 INSERT INTO app.studio_execution_receipts(id,user_id,intent_id,kind,bucket,object_key,storage_version_id,sha256)
 VALUES(gen_random_uuid(),j.user_id,p_id,p_kind,p_bucket,p_key,p_version,p_hash) ON CONFLICT(intent_id,kind,sha256) DO NOTHING;
END $$;

CREATE FUNCTION app.complete_studio_intent(p_id uuid,p_owner text,p_fence bigint,p_output uuid,p_version uuid,p_asset uuid,
 p_bucket text,p_key text,p_storage_version text,p_hash text,p_bytes bigint,p_mime text,p_provenance jsonb) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE j app.studio_execution_intents; p app.studio_projects; current_plan uuid; current_scope boolean;
BEGIN
 SELECT * INTO j FROM app.studio_execution_intents WHERE id=p_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'studio_intent_not_found'; END IF;
 PERFORM set_config('app.user_id',j.user_id::text,true);
 SELECT * INTO p FROM app.studio_projects WHERE id=j.project_id;
 PERFORM 1 FROM app.studio_skus WHERE id=p.sku_id FOR UPDATE;
 SELECT * INTO p FROM app.studio_projects WHERE id=j.project_id FOR UPDATE;
 SELECT * INTO j FROM app.studio_execution_intents WHERE id=p_id FOR UPDATE;
 IF p_fence IS NULL OR p_owner IS NULL OR j.lease_until IS NULL OR j.fencing_token<>p_fence OR j.lease_owner IS DISTINCT FROM p_owner OR j.lease_until<now()
 OR j.status NOT IN ('submitted','uncertain') THEN RAISE EXCEPTION 'stale_studio_lease' USING ERRCODE='55000'; END IF;
 SELECT id INTO current_plan FROM app.studio_plans WHERE slot_id=j.slot_id ORDER BY revision DESC LIMIT 1;
 current_scope:=NOT j.cancel_requested AND current_plan=j.plan_id AND EXISTS(SELECT 1 FROM app.studio_slots WHERE id=j.slot_id AND NOT archived)
 AND NOT EXISTS(SELECT 1 FROM app.studio_plan_facts pf JOIN app.studio_fact_versions f ON f.id=pf.fact_version_id
 WHERE pf.plan_id=j.plan_id AND EXISTS(SELECT 1 FROM app.studio_fact_versions newer WHERE newer.sku_id=f.sku_id AND newer.fact_key=f.fact_key AND newer.revision>f.revision));
 PERFORM app.register_studio_candidate(p_version,p_asset,j.project_id,j.source_asset_version_id,p_bucket,p_key,p_storage_version,p_hash,
 (p.output_spec->>'width')::integer,(p.output_spec->>'height')::integer,p_bytes,p_mime);
 INSERT INTO app.studio_outputs(id,user_id,product_id,project_id,slot_id,plan_id,asset_version_id,source_asset_version_id,route,
 provenance,idempotency_key,request_hash,execution_intent_id)
 VALUES(p_output,j.user_id,j.product_id,j.project_id,j.slot_id,j.plan_id,p_version,j.source_asset_version_id,'comfyui',p_provenance,
 'comfy:'||p_id::text,j.request_hash,p_id);
 IF current_scope THEN UPDATE app.studio_slots SET current_output_id=p_output WHERE id=j.slot_id; END IF;
 UPDATE app.studio_projects SET revision=revision+1 WHERE id=j.project_id;
 UPDATE app.studio_execution_intents SET status='succeeded',result=jsonb_build_object('output_id',p_output,'review_state','pending',
 'current_scope',current_scope,'real_model_execution',false),lease_owner=NULL,lease_until=NULL,updated_at=now() WHERE id=p_id;
 RETURN jsonb_build_object('output_id',p_output,'current_scope',current_scope);
END $$;

ALTER FUNCTION ops.claim_studio_intent(uuid,text) OWNER TO app_executor;
ALTER FUNCTION ops.advance_studio_intent(uuid,text,bigint,text,jsonb) OWNER TO app_executor;
ALTER FUNCTION app.cancel_studio_intent(uuid) OWNER TO app_executor;
ALTER FUNCTION ops.append_studio_receipt(uuid,text,text,text,text,text) OWNER TO app_executor;
ALTER FUNCTION app.complete_studio_intent(uuid,text,bigint,uuid,uuid,uuid,text,text,text,text,bigint,text,jsonb) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.claim_studio_intent(uuid,text),ops.advance_studio_intent(uuid,text,bigint,text,jsonb),
 app.cancel_studio_intent(uuid),ops.append_studio_receipt(uuid,text,text,text,text,text),
 app.complete_studio_intent(uuid,text,bigint,uuid,uuid,uuid,text,text,text,text,bigint,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.claim_studio_intent(uuid,text),ops.advance_studio_intent(uuid,text,bigint,text,jsonb),
 ops.append_studio_receipt(uuid,text,text,text,text,text),app.complete_studio_intent(uuid,text,bigint,uuid,uuid,uuid,text,text,text,text,bigint,text,jsonb) TO app_worker;
GRANT EXECUTE ON FUNCTION app.cancel_studio_intent(uuid) TO app_api;
GRANT UPDATE(id) ON app.studio_skus TO app_executor;
