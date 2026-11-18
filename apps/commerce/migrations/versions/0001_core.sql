CREATE SCHEMA app;
CREATE SCHEMA auth;
CREATE SCHEMA ops;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE ROLE app_api NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE app_auth NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE app_worker NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE ops_dispatcher NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE ops_admin NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE app_readonly NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE TABLE app.users (
 id uuid PRIMARY KEY, email_normalized text NOT NULL UNIQUE,
 display_name text NOT NULL, status text NOT NULL CHECK(status IN ('active','disabled')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE auth.credentials (
 user_id uuid PRIMARY KEY REFERENCES app.users(id) ON DELETE RESTRICT,
 password_hash text NOT NULL, password_version integer NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE auth.refresh_sessions (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
 family_id uuid NOT NULL, token_digest text NOT NULL UNIQUE, expires_at timestamptz NOT NULL,
 revoked_at timestamptz, replaced_by uuid REFERENCES auth.refresh_sessions(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.products (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 255), category text NOT NULL,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0), archived_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id)
);
CREATE TABLE app.appearance_profiles (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL,
 name text NOT NULL, config jsonb NOT NULL CHECK(jsonb_typeof(config)='object'),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0), archived_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), UNIQUE(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id) REFERENCES app.products(user_id,id) ON DELETE RESTRICT
);
CREATE TABLE app.assets (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('original','generated','final','preview')),
 status text NOT NULL CHECK(status IN ('uploading','validating','ready','rejected')),
 current_version_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), UNIQUE(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id) REFERENCES app.products(user_id,id) ON DELETE RESTRICT,
 CHECK(status<>'ready' OR current_version_id IS NOT NULL)
);
CREATE TABLE app.asset_versions (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, asset_id uuid NOT NULL,
 version_number integer NOT NULL CHECK(version_number>0), bucket text NOT NULL, object_key text NOT NULL,
 storage_version_id text, sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'), mime text NOT NULL,
 width integer NOT NULL CHECK(width>0), height integer NOT NULL CHECK(height>0), byte_size bigint NOT NULL CHECK(byte_size>0),
 color_space text NOT NULL, canonical boolean NOT NULL, source_version_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), UNIQUE(user_id,product_id,id), UNIQUE(user_id,product_id,asset_id,id),
 UNIQUE(asset_id,version_number), UNIQUE(bucket,object_key),
 FOREIGN KEY(user_id,product_id,asset_id) REFERENCES app.assets(user_id,product_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(user_id,product_id,source_version_id) REFERENCES app.asset_versions(user_id,product_id,id) ON DELETE RESTRICT
);
ALTER TABLE app.assets ADD CONSTRAINT current_version_owned FOREIGN KEY(user_id,product_id,id,current_version_id)
 REFERENCES app.asset_versions(user_id,product_id,asset_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE app.wallets (
 user_id uuid PRIMARY KEY REFERENCES app.users(id) ON DELETE RESTRICT,
 available bigint NOT NULL DEFAULT 0 CHECK(available>=0), reserved bigint NOT NULL DEFAULT 0 CHECK(reserved>=0),
 funded_total bigint NOT NULL DEFAULT 0 CHECK(funded_total>=0), spent_total bigint NOT NULL DEFAULT 0 CHECK(spent_total>=0),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(available+reserved=funded_total-spent_total)
);
ALTER TABLE app.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.products FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.products USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.appearance_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.appearance_profiles FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.appearance_profiles USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.assets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.assets USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.asset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.asset_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.asset_versions USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.wallets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.wallets USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid)
 WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.users FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.users USING(id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY auth_identity ON app.users TO app_auth USING(true);
GRANT USAGE ON SCHEMA app TO app_api,app_worker,app_auth,app_readonly;
GRANT USAGE ON SCHEMA auth TO app_auth;
GRANT SELECT(id,email_normalized,display_name,status) ON app.users TO app_api,app_auth;
GRANT SELECT,INSERT,UPDATE ON app.products,app.appearance_profiles TO app_api;
GRANT SELECT ON app.assets,app.asset_versions,app.wallets TO app_api;
GRANT SELECT,INSERT,UPDATE ON auth.credentials,auth.refresh_sessions TO app_auth;
ALTER TABLE auth.credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.credentials FORCE ROW LEVEL SECURITY;
CREATE POLICY authentication ON auth.credentials TO app_auth USING(true) WITH CHECK(true);
ALTER TABLE auth.refresh_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.refresh_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY authentication ON auth.refresh_sessions TO app_auth USING(true) WITH CHECK(true);
