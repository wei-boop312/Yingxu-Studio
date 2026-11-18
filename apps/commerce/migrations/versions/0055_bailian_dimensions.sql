-- Supplier-specific dimensions are validated before quote/task creation, not after a freeze.
CREATE FUNCTION app.enforce_bailian_dimensions() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE dimensions jsonb; w bigint; h bigint;
BEGIN
 IF NEW.input_snapshot->'_execution'->'provider_models'->>'generate' IS DISTINCT FROM 'qwen-image-3.0' THEN RETURN NEW; END IF;
 dimensions:=NEW.input_snapshot->'generation'->'output';
 IF jsonb_typeof(dimensions) IS DISTINCT FROM 'object'
 OR jsonb_typeof(dimensions->'width') IS DISTINCT FROM 'number'
 OR jsonb_typeof(dimensions->'height') IS DISTINCT FROM 'number'
 OR dimensions->>'width' !~ '^[0-9]{1,4}$' OR dimensions->>'height' !~ '^[0-9]{1,4}$'
 THEN RAISE EXCEPTION 'bailian_output_dimensions_out_of_range'; END IF;
 w:=(dimensions->>'width')::bigint; h:=(dimensions->>'height')::bigint;
 IF w NOT BETWEEN 256 AND 6256 OR h NOT BETWEEN 256 AND 6256
 OR w*h NOT BETWEEN 262144 AND 4000000 OR greatest(w,h)>8*least(w,h)
 THEN RAISE EXCEPTION 'bailian_output_dimensions_out_of_range'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION app.enforce_bailian_dimensions() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.enforce_bailian_dimensions() FROM PUBLIC;
CREATE TRIGGER enforce_bailian_dimensions BEFORE INSERT ON app.quotes FOR EACH ROW EXECUTE FUNCTION app.enforce_bailian_dimensions();
CREATE TRIGGER enforce_bailian_dimensions BEFORE INSERT ON app.generation_tasks FOR EACH ROW EXECUTE FUNCTION app.enforce_bailian_dimensions();
