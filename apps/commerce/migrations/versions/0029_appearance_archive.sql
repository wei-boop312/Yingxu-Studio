-- FOR SHARE requires an UPDATE privilege; expose only this column to the NOLOGIN
-- executor, matching the existing product-archive lock permission in 0004.
GRANT UPDATE(archived_at) ON app.appearance_profiles TO app_executor;

CREATE FUNCTION app.lock_active_appearance() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE appearance uuid; product uuid;
BEGIN
 IF TG_TABLE_NAME='quotes' THEN
  appearance:=(NEW.input_snapshot->>'appearance_profile_id')::uuid;
  product:=(NEW.input_snapshot->>'product_id')::uuid;
 ELSE
  appearance:=NEW.appearance_profile_id;
  product:=NEW.product_id;
 END IF;
 IF appearance IS NOT NULL THEN
  PERFORM 1 FROM app.appearance_profiles WHERE user_id=NEW.user_id AND product_id=product AND id=appearance AND archived_at IS NULL FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'appearance_unavailable'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app.lock_active_appearance() FROM PUBLIC;
CREATE TRIGGER quote_active_appearance BEFORE INSERT ON app.quotes FOR EACH ROW EXECUTE FUNCTION app.lock_active_appearance();
CREATE TRIGGER task_active_appearance BEFORE INSERT ON app.generation_tasks FOR EACH ROW EXECUTE FUNCTION app.lock_active_appearance();
