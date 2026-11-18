GRANT SELECT ON app.studio_projects TO app_executor;
CREATE FUNCTION app.register_studio_candidate(p_version uuid,p_asset uuid,p_project uuid,p_source uuid,
 p_bucket text,p_key text,p_storage_version text,p_hash text,p_width integer,p_height integer,p_bytes bigint,p_mime text)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; p app.studio_projects;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 SELECT * INTO p FROM app.studio_projects WHERE id=p_project AND user_id=u;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM app.asset_versions WHERE id=p_source AND user_id=u AND product_id=p.product_id)
 THEN RAISE EXCEPTION 'studio_source_scope_mismatch'; END IF;
 IF p_key IS DISTINCT FROM 'studio/'||u::text||'/'||p_project::text||'/'||p_version::text
 OR p_mime NOT IN ('image/png','image/jpeg') OR p_hash !~ '^[0-9a-f]{64}$'
 OR p_width<>(p.output_spec->>'width')::integer OR p_height<>(p.output_spec->>'height')::integer
 OR p_bytes<=0 OR p_bytes>(p.output_spec->>'max_bytes')::bigint
 THEN RAISE EXCEPTION 'invalid_studio_candidate'; END IF;
 INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(p_asset,u,p.product_id,'final','uploading');
 INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,storage_version_id,
  sha256,mime,width,height,byte_size,color_space,canonical,source_version_id)
 VALUES(p_version,u,p.product_id,p_asset,1,p_bucket,p_key,p_storage_version,p_hash,p_mime,p_width,p_height,p_bytes,'sRGB',p_mime='image/png',p_source);
 UPDATE app.assets SET status='ready',current_version_id=p_version WHERE id=p_asset AND user_id=u;
END $$;
ALTER FUNCTION app.register_studio_candidate(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.register_studio_candidate(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.register_studio_candidate(uuid,uuid,uuid,uuid,text,text,text,text,integer,integer,bigint,text) TO app_api;
ALTER TABLE app.studio_outputs ADD CONSTRAINT studio_output_slot_identity UNIQUE(user_id,project_id,slot_id,id);
ALTER TABLE app.studio_slots ADD COLUMN current_output_id uuid;
ALTER TABLE app.studio_slots ADD CONSTRAINT studio_current_output_matches_slot
 FOREIGN KEY(user_id,project_id,id,current_output_id) REFERENCES app.studio_outputs(user_id,project_id,slot_id,id);
