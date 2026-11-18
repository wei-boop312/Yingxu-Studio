CREATE TABLE app.studio_skus (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL,
 code text NOT NULL CHECK(length(code) BETWEEN 1 AND 80), name text NOT NULL CHECK(length(name) BETWEEN 1 AND 255),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,product_id,id),
 UNIQUE(user_id,product_id,code), FOREIGN KEY(user_id,product_id) REFERENCES app.products(user_id,id)
);
CREATE TABLE app.studio_projects (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, sku_id uuid NOT NULL,
 title text NOT NULL, marketplace text NOT NULL, language text NOT NULL, platform text NOT NULL,
 category text NOT NULL, image_use text NOT NULL, output_spec jsonb NOT NULL,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), UNIQUE(user_id,sku_id,id), UNIQUE(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,sku_id) REFERENCES app.studio_skus(user_id,product_id,id)
);
CREATE TABLE app.studio_references (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, sku_id uuid NOT NULL,
 asset_version_id uuid NOT NULL, role text NOT NULL CHECK(role IN ('product','packaging','accessory','specification','style','scene')),
 physical_face text NOT NULL, confirmed boolean NOT NULL DEFAULT false, note text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,sku_id,id),
 FOREIGN KEY(user_id,product_id,sku_id) REFERENCES app.studio_skus(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,asset_version_id) REFERENCES app.asset_versions(user_id,product_id,id)
);
CREATE TABLE app.studio_fact_versions (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, sku_id uuid NOT NULL, fact_key text NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), label text NOT NULL, value text NOT NULL,
 status text NOT NULL CHECK(status IN ('confirmed','unknown','conflict')), conditions text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,sku_id,id),
 UNIQUE(user_id,sku_id,fact_key,revision), FOREIGN KEY(user_id,sku_id) REFERENCES app.studio_skus(user_id,id)
);
CREATE TABLE app.studio_fact_sources (
 user_id uuid NOT NULL, sku_id uuid NOT NULL, fact_version_id uuid NOT NULL, reference_id uuid NOT NULL,
 PRIMARY KEY(fact_version_id,reference_id),
 FOREIGN KEY(user_id,sku_id,fact_version_id) REFERENCES app.studio_fact_versions(user_id,sku_id,id),
 FOREIGN KEY(user_id,sku_id,reference_id) REFERENCES app.studio_references(user_id,sku_id,id)
);
CREATE TABLE app.studio_slots (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, project_id uuid NOT NULL, display_number integer NOT NULL CHECK(display_number>0),
 title text NOT NULL, purpose text NOT NULL, required_fact_keys text[] NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,project_id,id),
 UNIQUE(project_id,display_number) DEFERRABLE INITIALLY IMMEDIATE,
 FOREIGN KEY(user_id,project_id) REFERENCES app.studio_projects(user_id,id)
);
CREATE TABLE app.studio_plans (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, sku_id uuid NOT NULL, project_id uuid NOT NULL, slot_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), snapshot jsonb NOT NULL, sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,sku_id,id),
 UNIQUE(user_id,project_id,slot_id,id), UNIQUE(slot_id,revision),
 FOREIGN KEY(user_id,sku_id,project_id) REFERENCES app.studio_projects(user_id,sku_id,id),
 FOREIGN KEY(user_id,project_id,slot_id) REFERENCES app.studio_slots(user_id,project_id,id)
);
CREATE TABLE app.studio_plan_facts (
 user_id uuid NOT NULL, sku_id uuid NOT NULL, plan_id uuid NOT NULL, fact_version_id uuid NOT NULL,
 PRIMARY KEY(plan_id,fact_version_id),
 FOREIGN KEY(user_id,sku_id,plan_id) REFERENCES app.studio_plans(user_id,sku_id,id),
 FOREIGN KEY(user_id,sku_id,fact_version_id) REFERENCES app.studio_fact_versions(user_id,sku_id,id)
);
CREATE TABLE app.studio_plan_references (
 user_id uuid NOT NULL, sku_id uuid NOT NULL, plan_id uuid NOT NULL, reference_id uuid NOT NULL,
 PRIMARY KEY(plan_id,reference_id),
 FOREIGN KEY(user_id,sku_id,plan_id) REFERENCES app.studio_plans(user_id,sku_id,id),
 FOREIGN KEY(user_id,sku_id,reference_id) REFERENCES app.studio_references(user_id,sku_id,id)
);
CREATE TABLE app.studio_outputs (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, product_id uuid NOT NULL, project_id uuid NOT NULL, slot_id uuid NOT NULL,
 plan_id uuid NOT NULL, asset_version_id uuid NOT NULL, source_asset_version_id uuid NOT NULL,
 route text NOT NULL CHECK(route IN ('manual_import','offline_fixture','comfyui')),
 task_id uuid, provenance jsonb NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,project_id,id),
 UNIQUE(user_id,idempotency_key),
 FOREIGN KEY(user_id,product_id,project_id) REFERENCES app.studio_projects(user_id,product_id,id),
 FOREIGN KEY(user_id,project_id,slot_id,plan_id) REFERENCES app.studio_plans(user_id,project_id,slot_id,id),
 FOREIGN KEY(user_id,product_id,asset_version_id) REFERENCES app.asset_versions(user_id,product_id,id),
 FOREIGN KEY(user_id,product_id,source_asset_version_id) REFERENCES app.asset_versions(user_id,product_id,id),
 FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id),
 CHECK(route<>'comfyui' OR task_id IS NOT NULL)
);
CREATE TABLE app.studio_reviews (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, project_id uuid NOT NULL, output_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), decision text NOT NULL CHECK(decision IN ('passed','rework','insufficient_evidence')),
 critical boolean NOT NULL, checks jsonb NOT NULL, notes text NOT NULL, basis jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(user_id,id), UNIQUE(user_id,project_id,id),
 UNIQUE(output_id,revision), CHECK(decision<>'passed' OR NOT critical),
 FOREIGN KEY(user_id,project_id,output_id) REFERENCES app.studio_outputs(user_id,project_id,id)
);
CREATE TABLE app.studio_deliveries (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, project_id uuid NOT NULL, project_revision bigint NOT NULL,
 mode text NOT NULL CHECK(mode IN ('draft','formal')), status text NOT NULL CHECK(status IN ('preparing','ready','failed')),
 manifest jsonb NOT NULL, object_key text, sha256 text,
 idempotency_key text NOT NULL, request_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), UNIQUE(user_id,project_id,id), UNIQUE(user_id,idempotency_key),
 FOREIGN KEY(user_id,project_id) REFERENCES app.studio_projects(user_id,id),
 CHECK(status<>'ready' OR (object_key IS NOT NULL AND sha256 ~ '^[0-9a-f]{64}$'))
);
CREATE TABLE app.studio_delivery_items (
 user_id uuid NOT NULL, project_id uuid NOT NULL, delivery_id uuid NOT NULL, output_id uuid NOT NULL, review_id uuid,
 PRIMARY KEY(delivery_id,output_id),
 FOREIGN KEY(user_id,project_id,delivery_id) REFERENCES app.studio_deliveries(user_id,project_id,id),
 FOREIGN KEY(user_id,project_id,output_id) REFERENCES app.studio_outputs(user_id,project_id,id),
 FOREIGN KEY(user_id,project_id,review_id) REFERENCES app.studio_reviews(user_id,project_id,id)
);
CREATE TABLE app.studio_cases (
 id uuid PRIMARY KEY, user_id uuid NOT NULL, project_id uuid NOT NULL, delivery_id uuid NOT NULL,
 title text NOT NULL, lessons text NOT NULL, snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,id), FOREIGN KEY(user_id,project_id) REFERENCES app.studio_projects(user_id,id),
 FOREIGN KEY(user_id,project_id,delivery_id) REFERENCES app.studio_deliveries(user_id,project_id,id)
);

DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['studio_skus','studio_projects','studio_references','studio_fact_versions',
  'studio_fact_sources','studio_slots','studio_plans','studio_plan_facts','studio_plan_references',
  'studio_outputs','studio_reviews','studio_deliveries','studio_delivery_items','studio_cases'] LOOP
  EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant ON app.%I USING(user_id=NULLIF(current_setting(''app.user_id'',true),'''')::uuid) WITH CHECK(user_id=NULLIF(current_setting(''app.user_id'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT,INSERT ON app.%I TO app_api',t);
  EXECUTE format('GRANT SELECT ON app.%I TO app_readonly',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['studio_references','studio_fact_versions','studio_fact_sources','studio_plans',
  'studio_plan_facts','studio_plan_references','studio_outputs','studio_reviews','studio_delivery_items','studio_cases'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_studio BEFORE UPDATE OR DELETE ON app.%I FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()',t);
 END LOOP;
END $$;
GRANT UPDATE ON app.studio_projects,app.studio_slots,app.studio_deliveries TO app_api;
CREATE INDEX studio_project_owner ON app.studio_projects(user_id,created_at);
CREATE INDEX studio_facts_latest ON app.studio_fact_versions(user_id,sku_id,fact_key,revision DESC);
CREATE INDEX studio_plan_slot ON app.studio_plans(slot_id,revision DESC);
CREATE INDEX studio_output_slot ON app.studio_outputs(slot_id,created_at DESC);
CREATE INDEX studio_review_output ON app.studio_reviews(output_id,revision DESC);
