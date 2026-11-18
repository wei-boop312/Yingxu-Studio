CREATE ROLE app_provisioner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA app,auth TO app_provisioner;
GRANT INSERT(id,email_normalized,display_name,status) ON app.users TO app_provisioner;
GRANT INSERT(user_id,password_hash) ON auth.credentials TO app_provisioner;
CREATE POLICY private_provision ON app.users FOR INSERT TO app_provisioner WITH CHECK(true);
CREATE POLICY private_provision ON auth.credentials FOR INSERT TO app_provisioner WITH CHECK(true);
CREATE FUNCTION app.create_private_user(p_id uuid,p_email text,p_name text,p_password_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_email IS NULL OR length(p_email) NOT BETWEEN 3 AND 320 OR p_email<>lower(trim(p_email))
 OR p_name IS NULL OR length(p_name) NOT BETWEEN 1 AND 255 OR p_password_hash NOT LIKE '$argon2id$%' THEN RAISE EXCEPTION 'invalid_private_account'; END IF;
 INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(p_id,p_email,p_name,'active');
 INSERT INTO auth.credentials(user_id,password_hash) VALUES(p_id,p_password_hash);
 RETURN p_id;
END $$;
ALTER FUNCTION app.create_private_user(uuid,text,text,text) OWNER TO app_provisioner;
REVOKE ALL ON FUNCTION app.create_private_user(uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.create_private_user(uuid,text,text,text) TO ops_admin;
