CREATE FUNCTION app.enforce_qwen_source_limits() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source app.asset_versions;
BEGIN
 IF NEW.input_snapshot->'_execution'->'provider_models'->>'generate' IS DISTINCT FROM 'qwen-image-3.0' THEN RETURN NEW; END IF;
 -- Ownership comes from the trusted row, never an optional business JSON field.
 SELECT * INTO source FROM app.asset_versions
 WHERE id=(NEW.input_snapshot->>'source_asset_version_id')::uuid AND user_id=NEW.user_id
 AND product_id=(NEW.input_snapshot->>'product_id')::uuid;
 IF NOT FOUND OR NOT source.canonical OR source.mime<>'image/png'
 OR source.width NOT BETWEEN 1 AND 8000 OR source.height NOT BETWEEN 1 AND 8000
 OR greatest(source.width,source.height)::bigint>10*least(source.width,source.height)::bigint
 OR source.byte_size>10000000 THEN RAISE EXCEPTION 'bailian_source_out_of_range'; END IF;
 IF source.sha256 IS DISTINCT FROM NEW.input_snapshot->'_execution'->>'source_sha256'
 OR source.width::text IS DISTINCT FROM NEW.input_snapshot->'_execution'->>'source_width'
 OR source.height::text IS DISTINCT FROM NEW.input_snapshot->'_execution'->>'source_height'
 THEN RAISE EXCEPTION 'bailian_source_metadata_mismatch'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION app.enforce_qwen_source_limits() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.enforce_qwen_source_limits() FROM PUBLIC;
CREATE TRIGGER enforce_qwen_source_limits BEFORE INSERT ON app.quotes
FOR EACH ROW EXECUTE FUNCTION app.enforce_qwen_source_limits();
CREATE TRIGGER enforce_qwen_source_limits BEFORE INSERT ON app.generation_tasks
FOR EACH ROW EXECUTE FUNCTION app.enforce_qwen_source_limits();
