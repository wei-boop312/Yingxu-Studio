CREATE TABLE auth.session_families (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.users(id), csrf_digest text NOT NULL,
 expires_at timestamptz NOT NULL, revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE auth.refresh_sessions ADD CONSTRAINT refresh_family FOREIGN KEY(family_id) REFERENCES auth.session_families(id);
CREATE INDEX refresh_family_idx ON auth.refresh_sessions(family_id);
CREATE TABLE auth.login_limits (
 bucket text PRIMARY KEY, window_start timestamptz NOT NULL DEFAULT now(), attempts integer NOT NULL CHECK(attempts>0)
);
ALTER TABLE auth.session_families ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.session_families FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.login_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.login_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY authentication ON auth.session_families TO app_auth USING(true) WITH CHECK(true);
CREATE POLICY authentication ON auth.login_limits TO app_auth USING(true) WITH CHECK(true);
GRANT SELECT,INSERT,UPDATE ON auth.session_families,auth.login_limits TO app_auth;
