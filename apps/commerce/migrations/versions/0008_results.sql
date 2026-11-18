CREATE TABLE app.upload_intents (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, asset_id uuid NOT NULL,
 temp_key text NOT NULL UNIQUE, expected_mime text NOT NULL CHECK(expected_mime IN ('image/jpeg','image/png','image/webp')),
 expected_size bigint NOT NULL CHECK(expected_size BETWEEN 1 AND 20971520), expires_at timestamptz NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','validating','ready','rejected')), completed_version_id uuid,
 original_version_id uuid, lease_owner uuid, lease_until timestamptz, error_code text, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), FOREIGN KEY(user_id,product_id,asset_id) REFERENCES app.assets(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,asset_id,completed_version_id) REFERENCES app.asset_versions(user_id,product_id,asset_id,id),
 FOREIGN KEY(user_id,product_id,asset_id,original_version_id) REFERENCES app.asset_versions(user_id,product_id,asset_id,id),
 CHECK(status<>'ready' OR (completed_version_id IS NOT NULL AND original_version_id IS NOT NULL))
);
ALTER TABLE app.provider_attempts ADD CONSTRAINT attempt_task_owned UNIQUE(user_id,task_id,id);
CREATE TABLE app.task_cost_records (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, task_id uuid NOT NULL, attempt_id uuid NOT NULL,
 cost_record_kind text NOT NULL CHECK(cost_record_kind IN ('estimate','settlement')), provider text NOT NULL, model text NOT NULL,
 currency text NOT NULL, amount numeric(20,8) CHECK(amount>=0), cost_status text NOT NULL CHECK(cost_status IN ('estimated','confirmed','unknown')),
 usage jsonb NOT NULL DEFAULT '{}', price_source text NOT NULL, price_version text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(attempt_id,cost_record_kind), FOREIGN KEY(user_id,task_id,attempt_id) REFERENCES app.provider_attempts(user_id,task_id,id),
 CHECK((cost_status='unknown' AND amount IS NULL) OR (cost_status<>'unknown' AND amount IS NOT NULL))
);
CREATE TABLE app.compliance_checks (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, task_id uuid NOT NULL,
 final_version_id uuid NOT NULL, final_sha256 text NOT NULL CHECK(final_sha256 ~ '^[0-9a-f]{64}$'), facts jsonb NOT NULL,
 facts_schema_version integer NOT NULL CHECK(facts_schema_version=1), rule_snapshots jsonb NOT NULL, per_platform_results jsonb NOT NULL,
 auto_overall text NOT NULL CHECK(auto_overall IN ('compliant','non_compliant','manual_review')),
 sampling_selected boolean NOT NULL, report_sha256 text NOT NULL CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
 report_bucket text NOT NULL, report_object_key text NOT NULL, report_storage_version_id text,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,task_id,id),
 FOREIGN KEY(user_id,product_id,task_id) REFERENCES app.generation_tasks(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,final_version_id) REFERENCES app.asset_versions(user_id,product_id,id)
);
ALTER TABLE app.generation_tasks ADD CONSTRAINT compliance_task_owned FOREIGN KEY(user_id,id,compliance_id) REFERENCES app.compliance_checks(user_id,task_id,id);
CREATE TABLE app.compliance_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, check_id uuid NOT NULL,
 reviewer_id uuid NOT NULL REFERENCES app.users(id), operation_id uuid NOT NULL UNIQUE,
 decision text NOT NULL CHECK(decision IN ('approved','rejected','needs_review')), reason text NOT NULL CHECK(length(reason)>0),
 evidence_refs jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,check_id) REFERENCES app.compliance_checks(user_id,id)
);
CREATE TABLE ops.provider_receipts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id uuid NOT NULL, user_id uuid NOT NULL, attempt_id uuid NOT NULL,
 provider_id text NOT NULL, response_digest text NOT NULL, polling_url_private text, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,task_id,attempt_id) REFERENCES app.provider_attempts(user_id,task_id,id), UNIQUE(attempt_id,provider_id,response_digest)
);
CREATE TRIGGER immutable_versions BEFORE UPDATE OR DELETE ON app.asset_versions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER immutable_compliance BEFORE UPDATE OR DELETE ON app.compliance_checks FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER immutable_reviews BEFORE UPDATE OR DELETE ON app.compliance_reviews FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER immutable_cost BEFORE UPDATE OR DELETE ON app.task_cost_records FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER immutable_receipts BEFORE UPDATE OR DELETE ON ops.provider_receipts FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.upload_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.upload_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.upload_intents USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.task_cost_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.task_cost_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.task_cost_records USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.compliance_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.compliance_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.compliance_checks USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.compliance_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.compliance_reviews FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.compliance_reviews USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE ops.provider_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY executor_receipts ON ops.provider_receipts TO app_executor USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
GRANT SELECT ON app.upload_intents,app.compliance_checks,app.compliance_reviews TO app_api;
GRANT SELECT,INSERT,UPDATE ON app.upload_intents,app.assets TO app_executor;
GRANT INSERT ON app.asset_versions TO app_executor;
GRANT SELECT,INSERT ON app.compliance_checks,app.compliance_reviews,app.task_cost_records,ops.provider_receipts TO app_executor;
GRANT INSERT,UPDATE ON app.provider_attempts TO app_executor;
GRANT UPDATE ON ops.task_dispatch,ops.outbox TO app_executor;
CREATE POLICY executor_dispatch_lookup ON ops.task_dispatch FOR SELECT TO app_executor USING(true);
CREATE POLICY executor_outbox_lookup ON ops.outbox FOR SELECT TO app_executor USING(true);
GRANT USAGE ON SCHEMA ops,app TO app_worker;
