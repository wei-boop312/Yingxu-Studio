CREATE TABLE app.appearance_understandings (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL,
 source_asset_version_id uuid NOT NULL, source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 runtime_sha256 text NOT NULL CHECK(runtime_sha256 ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'quoted' CHECK(status IN ('quoted','submitting','succeeded','failed','uncertain')),
 result jsonb, response_sha256 text CHECK(response_sha256 ~ '^[0-9a-f]{64}$'), metering jsonb,
 error_code text, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '10 minutes',
 confirmed_profile_id uuid,
 UNIQUE(user_id,id),
 FOREIGN KEY(user_id,product_id,source_asset_version_id) REFERENCES app.asset_versions(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,confirmed_profile_id) REFERENCES app.appearance_profiles(user_id,product_id,id)
);
ALTER TABLE app.appearance_understandings ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.appearance_understandings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.appearance_understandings
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON app.appearance_understandings TO app_api;
GRANT SELECT,UPDATE ON app.appearance_understandings TO app_executor;
CREATE FUNCTION ops.close_appearance_understanding(p_user uuid,p_id uuid,p_operation uuid,p_reason text,p_digest text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r app.appearance_understandings;
BEGIN
 IF p_reason IS NULL OR length(trim(p_reason))=0 OR length(p_reason)>4000 OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'evidence_required'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO r FROM app.appearance_understandings WHERE id=p_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'not_found'; END IF;
 IF EXISTS(SELECT 1 FROM ops.audit_events WHERE request_id=p_operation) THEN RAISE EXCEPTION 'operation_conflict'; END IF;
 IF r.status NOT IN ('submitting','uncertain') THEN RAISE EXCEPTION 'not_uncertain'; END IF;
 UPDATE app.appearance_understandings SET status='failed',error_code='manual_delivery_closed' WHERE id=p_id;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'close_appearance_understanding','appearance_understanding',p_id,p_reason,p_digest,jsonb_build_object('status',r.status),jsonb_build_object('status','failed','supplier_charge','unknown','supplier_refund_verified',false),p_operation);
END $$;
ALTER FUNCTION ops.close_appearance_understanding(uuid,uuid,uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.close_appearance_understanding(uuid,uuid,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.close_appearance_understanding(uuid,uuid,uuid,text,text) TO ops_admin;
