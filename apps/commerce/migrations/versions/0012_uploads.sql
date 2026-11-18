CREATE FUNCTION app.create_upload(p_id uuid,p_asset uuid,p_product uuid,p_mime text,p_size bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; key text;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 IF u IS NULL OR NOT EXISTS(SELECT 1 FROM app.users WHERE id=u AND status='active') THEN RAISE EXCEPTION 'inactive_user'; END IF;
 PERFORM 1 FROM app.products WHERE id=p_product AND user_id=u AND archived_at IS NULL FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'product_unavailable'; END IF;
 key:='temp/'||u::text||'/'||p_id::text;
 INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(p_asset,u,p_product,'original','uploading');
 INSERT INTO app.upload_intents(id,user_id,product_id,asset_id,temp_key,expected_mime,expected_size,expires_at,status)
 VALUES(p_id,u,p_product,p_asset,key,p_mime,p_size,now()+interval '15 minutes','pending');
 RETURN jsonb_build_object('intent_id',p_id,'asset_id',p_asset,'temp_key',key);
END $$;
CREATE FUNCTION app.claim_upload(p_intent uuid,p_owner uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; i app.upload_intents;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 SELECT * INTO i FROM app.upload_intents WHERE id=p_intent AND user_id=u FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'upload_not_found'; END IF;
 IF i.status='ready' THEN RETURN to_jsonb(i); END IF;
 IF i.status='rejected' THEN RAISE EXCEPTION 'upload_rejected'; END IF;
 IF i.expires_at<=now() AND i.status='pending' THEN RAISE EXCEPTION 'upload_expired'; END IF;
 IF i.lease_until>clock_timestamp() THEN RAISE EXCEPTION 'upload_busy'; END IF;
 UPDATE app.upload_intents SET status='validating',lease_owner=p_owner,lease_until=clock_timestamp()+interval '30 seconds' WHERE id=i.id RETURNING * INTO i;
 UPDATE app.assets SET status='validating' WHERE id=i.asset_id;
 RETURN to_jsonb(i);
END $$;
CREATE FUNCTION app.finish_upload(p_intent uuid,p_owner uuid,p_original uuid,p_canonical uuid,p_bucket text,p_original_key text,p_original_storage text,p_original_sha text,p_original_mime text,p_original_width integer,p_original_height integer,p_original_bytes bigint,p_canonical_key text,p_canonical_storage text,p_canonical_sha text,p_width integer,p_height integer,p_bytes bigint) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; i app.upload_intents;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 SELECT * INTO i FROM app.upload_intents WHERE id=p_intent AND user_id=u FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'upload_not_found'; END IF;
 IF i.status='ready' THEN RETURN i.completed_version_id; END IF;
 IF i.status<>'validating' OR i.lease_owner IS DISTINCT FROM p_owner OR i.lease_until IS NULL OR i.lease_until<=clock_timestamp() THEN RAISE EXCEPTION 'upload_lease_lost'; END IF;
 IF p_original_mime<>i.expected_mime OR p_original_bytes<>i.expected_size THEN RAISE EXCEPTION 'upload_metadata_mismatch'; END IF;
 INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,storage_version_id,sha256,mime,width,height,byte_size,color_space,canonical)
 VALUES(p_original,u,i.product_id,i.asset_id,1,p_bucket,p_original_key,p_original_storage,p_original_sha,p_original_mime,p_original_width,p_original_height,p_original_bytes,'original',false);
 INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,storage_version_id,sha256,mime,width,height,byte_size,color_space,canonical,source_version_id)
 VALUES(p_canonical,u,i.product_id,i.asset_id,2,p_bucket,p_canonical_key,p_canonical_storage,p_canonical_sha,'image/png',p_width,p_height,p_bytes,'sRGB',true,p_original);
 UPDATE app.assets SET status='ready',current_version_id=p_canonical WHERE id=i.asset_id;
 UPDATE app.upload_intents SET status='ready',original_version_id=p_original,completed_version_id=p_canonical,lease_owner=NULL,lease_until=NULL WHERE id=i.id;
 RETURN p_canonical;
END $$;
CREATE FUNCTION app.release_upload(p_intent uuid,p_owner uuid,p_code text,p_reject boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; i app.upload_intents;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 SELECT * INTO i FROM app.upload_intents WHERE id=p_intent AND user_id=u FOR UPDATE;
 IF NOT FOUND OR i.status='ready' OR i.lease_owner IS DISTINCT FROM p_owner THEN RETURN; END IF;
 UPDATE app.upload_intents SET status=CASE WHEN p_reject THEN 'rejected' ELSE 'pending' END,lease_owner=NULL,lease_until=NULL,error_code=p_code WHERE id=i.id;
 UPDATE app.assets SET status=CASE WHEN p_reject THEN 'rejected' ELSE 'uploading' END WHERE id=i.asset_id;
END $$;
ALTER FUNCTION app.create_upload(uuid,uuid,uuid,text,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.create_upload(uuid,uuid,uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.create_upload(uuid,uuid,uuid,text,bigint) TO app_api;
ALTER FUNCTION app.claim_upload(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.claim_upload(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.claim_upload(uuid,uuid) TO app_api;
ALTER FUNCTION app.finish_upload(uuid,uuid,uuid,uuid,text,text,text,text,text,integer,integer,bigint,text,text,text,integer,integer,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.finish_upload(uuid,uuid,uuid,uuid,text,text,text,text,text,integer,integer,bigint,text,text,text,integer,integer,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.finish_upload(uuid,uuid,uuid,uuid,text,text,text,text,text,integer,integer,bigint,text,text,text,integer,integer,bigint) TO app_api;
ALTER FUNCTION app.release_upload(uuid,uuid,text,boolean) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.release_upload(uuid,uuid,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.release_upload(uuid,uuid,text,boolean) TO app_api;
