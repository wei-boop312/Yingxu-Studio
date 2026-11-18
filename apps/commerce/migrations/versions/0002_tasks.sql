CREATE TABLE app.quotes (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.users(id), operation text NOT NULL DEFAULT 'generate',
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'), input_snapshot jsonb NOT NULL,
 input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'), price_version text NOT NULL, provider_model text NOT NULL, rules_snapshot jsonb NOT NULL,
 credits bigint NOT NULL CHECK(credits>0), expires_at timestamptz NOT NULL, consumed_task_id uuid UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id)
);
CREATE TABLE app.generation_tasks (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, appearance_profile_id uuid,
 source_asset_version_id uuid NOT NULL, operation text NOT NULL DEFAULT 'generate',
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 200), request_hash text NOT NULL,
 quote_id uuid NOT NULL UNIQUE, status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','succeeded','failed','cancelled','uncertain')),
 phase text NOT NULL DEFAULT 'queued' CHECK(phase IN ('queued','submitting','polling','archiving','protecting','detecting','validating_rules','finalizing')),
 credits_status text NOT NULL DEFAULT 'frozen' CHECK(credits_status IN ('frozen','charged','refunded')),
 reserved_credits bigint NOT NULL CHECK(reserved_credits>0), input_snapshot jsonb NOT NULL, input_sha256 text NOT NULL,
 final_version_id uuid, compliance_id uuid, cancel_requested_at timestamptz, lease_owner text, lease_until timestamptz,
 fencing_token bigint NOT NULL DEFAULT 0, revision bigint NOT NULL DEFAULT 1 CHECK(revision>0), error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 UNIQUE(user_id,id), UNIQUE(user_id,product_id,id), UNIQUE(user_id,operation,idempotency_key),
 FOREIGN KEY(user_id,product_id) REFERENCES app.products(user_id,id),
 FOREIGN KEY(user_id,product_id,appearance_profile_id) REFERENCES app.appearance_profiles(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,source_asset_version_id) REFERENCES app.asset_versions(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,final_version_id) REFERENCES app.asset_versions(user_id,product_id,id),
 FOREIGN KEY(user_id,quote_id) REFERENCES app.quotes(user_id,id),
 CHECK(status<>'succeeded' OR (final_version_id IS NOT NULL AND compliance_id IS NOT NULL AND completed_at IS NOT NULL AND credits_status='charged')),
 CHECK(status NOT IN ('failed','cancelled') OR (completed_at IS NOT NULL AND credits_status='refunded'))
);
ALTER TABLE app.quotes ADD CONSTRAINT consumed_task_owned FOREIGN KEY(user_id,consumed_task_id) REFERENCES app.generation_tasks(user_id,id);
CREATE TABLE app.credit_transactions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES app.wallets(user_id), task_id uuid,
 operation_id uuid NOT NULL UNIQUE, type text NOT NULL CHECK(type IN ('grant','freeze','charge','refund')),
 amount bigint NOT NULL CHECK(amount>0), available_delta bigint NOT NULL, reserved_delta bigint NOT NULL,
 funded_delta bigint NOT NULL, spent_delta bigint NOT NULL,
 available_after bigint NOT NULL CHECK(available_after>=0), reserved_after bigint NOT NULL CHECK(reserved_after>=0),
 actor_ref text NOT NULL, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id),
 CHECK((type='grant' AND task_id IS NULL AND available_delta=amount AND reserved_delta=0 AND funded_delta=amount AND spent_delta=0)
 OR (type='freeze' AND task_id IS NOT NULL AND available_delta=-amount AND reserved_delta=amount AND funded_delta=0 AND spent_delta=0)
 OR (type='charge' AND task_id IS NOT NULL AND available_delta=0 AND reserved_delta=-amount AND funded_delta=0 AND spent_delta=amount)
 OR (type='refund' AND task_id IS NOT NULL AND available_delta=amount AND reserved_delta=-amount AND funded_delta=0 AND spent_delta=0))
);
CREATE UNIQUE INDEX credit_once_per_task_type ON app.credit_transactions(task_id,type) WHERE task_id IS NOT NULL;
CREATE UNIQUE INDEX one_terminal_credit ON app.credit_transactions(task_id) WHERE type IN ('charge','refund');
CREATE TABLE app.task_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id uuid NOT NULL, task_id uuid NOT NULL,
 type text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id)
);
CREATE INDEX event_cursor ON app.task_events(user_id,task_id,id);
CREATE TABLE ops.outbox (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, task_id uuid NOT NULL, event_type text NOT NULL,
 dedupe_key text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(), publish_attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz NOT NULL DEFAULT now(), locked_until timestamptz, published_at timestamptz,
 FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id)
);
CREATE INDEX outbox_pending ON ops.outbox(next_attempt_at) WHERE published_at IS NULL;
CREATE TABLE ops.task_dispatch (
 task_id uuid PRIMARY KEY, user_id uuid NOT NULL, ready_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, fencing_token bigint NOT NULL DEFAULT 0, state text NOT NULL DEFAULT 'queued',
 FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id)
);
CREATE TABLE ops.audit_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), actor text NOT NULL, action text NOT NULL, target_type text NOT NULL,
 target_id uuid NOT NULL, reason text NOT NULL, evidence_digest text, before_state jsonb, after_state jsonb,
 request_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE app.provider_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, task_id uuid NOT NULL,
 step text NOT NULL CHECK(step IN ('generate','detect')), attempt_number integer NOT NULL DEFAULT 1 CHECK(attempt_number=1),
 provider text NOT NULL, model text NOT NULL, status text NOT NULL CHECK(status IN ('intent_recorded','submitted','polling','succeeded','failed','uncertain')),
 intent_at timestamptz NOT NULL DEFAULT now(), request_hash text NOT NULL, local_request_id uuid NOT NULL,
 provider_id text, polling_url_private text, response_digest text, error_code text,
 submitted_at timestamptz, last_polled_at timestamptz, finished_at timestamptz,
 UNIQUE(task_id,step), UNIQUE(user_id,id), FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id)
);
CREATE FUNCTION app.reject_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable_record' USING ERRCODE='55000'; END $$;
CREATE TRIGGER immutable_ledger BEFORE UPDATE OR DELETE ON app.credit_transactions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON ops.audit_events FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.quotes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.quotes USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.generation_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.generation_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.generation_tasks USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.credit_transactions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.credit_transactions USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.task_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.task_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.task_events USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE app.provider_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.provider_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON app.provider_attempts USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
ALTER TABLE ops.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY internal_dispatch ON ops.outbox TO ops_dispatcher USING(true) WITH CHECK(true);
ALTER TABLE ops.task_dispatch ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.task_dispatch FORCE ROW LEVEL SECURITY;
CREATE POLICY internal_dispatch ON ops.task_dispatch TO ops_dispatcher USING(true) WITH CHECK(true);
ALTER TABLE ops.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY internal_dispatch ON ops.audit_events TO ops_dispatcher USING(true) WITH CHECK(true);
GRANT SELECT ON app.quotes,app.generation_tasks,app.credit_transactions,app.task_events TO app_api;
GRANT USAGE ON SCHEMA ops TO ops_dispatcher,ops_admin;
GRANT SELECT,UPDATE ON ops.outbox,ops.task_dispatch TO ops_dispatcher;
REVOKE ALL ON FUNCTION app.reject_mutation() FROM PUBLIC;
