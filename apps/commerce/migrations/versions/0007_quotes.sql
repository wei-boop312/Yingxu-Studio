CREATE TABLE app.platform_rules (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), platform text NOT NULL, marketplace text NOT NULL, category text NOT NULL,
 image_type text NOT NULL, version text NOT NULL, evidence_status text NOT NULL CHECK(evidence_status IN ('unverified','verified')),
 source_urls jsonb NOT NULL DEFAULT '[]', retrieved_at timestamptz, config jsonb NOT NULL,
 config_sha256 text NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'), published_at timestamptz,
 UNIQUE(platform,marketplace,category,image_type,version)
);
ALTER TABLE app.platform_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.platform_rules FORCE ROW LEVEL SECURITY;
CREATE POLICY published_rules ON app.platform_rules FOR SELECT TO app_api,app_worker,app_executor,app_readonly USING(published_at IS NOT NULL);
GRANT SELECT ON app.platform_rules TO app_api,app_executor;
CREATE FUNCTION app.quote_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable_quote' USING ERRCODE='55000'; END IF;
 IF (to_jsonb(NEW)-'consumed_task_id') IS DISTINCT FROM (to_jsonb(OLD)-'consumed_task_id')
 OR (OLD.consumed_task_id IS NOT NULL AND NEW.consumed_task_id IS DISTINCT FROM OLD.consumed_task_id)
 THEN RAISE EXCEPTION 'immutable_quote' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable_quote BEFORE UPDATE OR DELETE ON app.quotes FOR EACH ROW EXECUTE FUNCTION app.quote_immutable();
REVOKE ALL ON FUNCTION app.quote_immutable() FROM PUBLIC;
CREATE FUNCTION app.create_quote(p_id uuid,p_input_hash text,p_snapshot_hash text,p_snapshot jsonb,p_rules jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; product uuid; source uuid; appearance uuid; v app.asset_versions;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 IF u IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.users WHERE id=u AND status='active') THEN RAISE EXCEPTION 'inactive_user'; END IF;
 IF p_input_hash !~ '^[0-9a-f]{64}$' OR p_snapshot_hash !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_snapshot)<>'object' THEN RAISE EXCEPTION 'invalid_quote'; END IF;
 product:=(p_snapshot->>'product_id')::uuid;
 source:=(p_snapshot->>'source_asset_version_id')::uuid;
 appearance:=(p_snapshot->>'appearance_profile_id')::uuid;
 PERFORM 1 FROM app.products WHERE user_id=u AND id=product AND archived_at IS NULL FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'product_unavailable'; END IF;
 SELECT av.* INTO v FROM app.asset_versions av JOIN app.assets a ON a.user_id=av.user_id AND a.id=av.asset_id
 WHERE av.user_id=u AND av.product_id=product AND av.id=source AND av.canonical AND a.status='ready';
 IF NOT FOUND THEN RAISE EXCEPTION 'source_unavailable'; END IF;
 IF p_snapshot->'_execution'->>'source_sha256' IS DISTINCT FROM v.sha256 THEN RAISE EXCEPTION 'source_hash_mismatch'; END IF;
 IF appearance IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.appearance_profiles WHERE user_id=u AND product_id=product AND id=appearance AND archived_at IS NULL) THEN RAISE EXCEPTION 'appearance_unavailable'; END IF;
 INSERT INTO app.quotes(id,user_id,input_hash,input_sha256,input_snapshot,price_version,provider_model,rules_snapshot,credits,expires_at)
 VALUES(p_id,u,p_input_hash,p_snapshot_hash,p_snapshot,'trial-fake-v1','fake-v1',p_rules,16,now()+interval '10 minutes');
 RETURN p_id;
END $$;
ALTER FUNCTION app.create_quote(uuid,text,text,jsonb,jsonb) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.create_quote(uuid,text,text,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.create_quote(uuid,text,text,jsonb,jsonb) TO app_api;
REVOKE EXECUTE ON FUNCTION app.submit_task(uuid,uuid,text,text) FROM app_api;
CREATE FUNCTION app.submit_quoted_task(p_id uuid,p_quote uuid,p_key text,p_hash text,p_input_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; q app.quotes;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 IF u IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
 SELECT * INTO q FROM app.quotes WHERE user_id=u AND id=p_quote;
 IF NOT FOUND THEN RAISE EXCEPTION 'quote_not_found'; END IF;
 IF q.input_hash IS DISTINCT FROM p_input_hash THEN RAISE EXCEPTION 'quote_input_mismatch'; END IF;
 RETURN app.submit_task(p_id,p_quote,p_key,p_hash);
END $$;
ALTER FUNCTION app.submit_quoted_task(uuid,uuid,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.submit_quoted_task(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.submit_quoted_task(uuid,uuid,text,text,text) TO app_api;
