-- No numeric price is seeded; an archive and explicit operator review are required.
CREATE TABLE ops.supplier_price_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), canonical_document text NOT NULL CHECK(octet_length(canonical_document)<=16384),
 document jsonb NOT NULL, document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),
 provider text NOT NULL, model text NOT NULL, endpoint_origin text NOT NULL, merchant_fingerprint text NOT NULL,
 billing_group text NOT NULL, version text NOT NULL, valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL,
 evidence_bucket text NOT NULL, evidence_object_key text NOT NULL, evidence_storage_version_id text,
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 actor text NOT NULL, reason text NOT NULL, operation_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(provider,model,endpoint_origin,merchant_fingerprint,billing_group,version)
);
ALTER TABLE ops.supplier_price_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.supplier_price_versions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.supplier_price_versions FROM PUBLIC;
GRANT SELECT,INSERT ON ops.supplier_price_versions TO app_executor;
GRANT SELECT ON ops.supplier_price_versions TO ops_admin;
CREATE POLICY supplier_price_executor ON ops.supplier_price_versions TO app_executor USING(true) WITH CHECK(true);
CREATE POLICY supplier_price_operator ON ops.supplier_price_versions FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_supplier_price BEFORE UPDATE OR DELETE ON ops.supplier_price_versions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION ops.publish_supplier_price(p_canonical text,p_sha text,p_bucket text,p_key text,p_storage_version text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; rate jsonb; field text; identity uuid; existing ops.supplier_price_versions; names text[]; started timestamptz; ended timestamptz;
BEGIN
 IF p_canonical IS NULL OR octet_length(p_canonical)>16384 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha
 OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_bucket IS NULL OR length(p_bucket) NOT BETWEEN 1 AND 255 OR p_key IS NULL OR length(p_key) NOT BETWEEN 1 AND 1024 THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object'
 OR NOT(d ?& ARRAY['schema_version','provider','model','endpoint_origin','merchant_fingerprint','billing_group','version','currency','valid_from','valid_until','evidence_kind','evidence_sha256','complete_meter_coverage','rates'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(d) k WHERE k NOT IN ('schema_version','provider','model','endpoint_origin','merchant_fingerprint','billing_group','version','currency','valid_from','valid_until','evidence_kind','evidence_sha256','complete_meter_coverage','rates'))
 OR d->'schema_version' IS DISTINCT FROM '1'::jsonb OR d->>'schema_version' IS DISTINCT FROM '1' OR d->'complete_meter_coverage' IS DISTINCT FROM 'true'::jsonb
 OR d->>'provider' NOT IN ('bfl','gemini') OR d->>'currency' NOT IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT')
 OR d->>'evidence_kind' NOT IN ('offline_fixture','reviewed_price') THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 FOREACH field IN ARRAY ARRAY['provider','model','endpoint_origin','merchant_fingerprint','billing_group','version','currency','valid_from','valid_until','evidence_kind','evidence_sha256'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 END LOOP;
 IF d->>'merchant_fingerprint' !~ '^[0-9a-f]{64}$' OR d->>'evidence_sha256' !~ '^[0-9a-f]{64}$'
 OR length(d->>'billing_group') NOT BETWEEN 1 AND 160 OR length(d->>'version') NOT BETWEEN 1 AND 160 OR trim(d->>'billing_group')='' OR trim(d->>'version')=''
 OR d->>'billing_group' ~ '[[:cntrl:]]' OR d->>'version' ~ '[[:cntrl:]]'
 OR (d->>'provider'='bfl' AND (d->>'model'<>'flux-3-image' OR d->>'endpoint_origin'<>'https://api.bfl.ai'))
 OR (d->>'provider'='gemini' AND (d->>'model'<>'gemini-3.8-flash' OR d->>'endpoint_origin' NOT IN ('https://generativelanguage.googleapis.com','https://hubway.cc','https://z.hubway.cc')))
 OR d->>'valid_from' !~ '(Z|[+]00:00)$' OR d->>'valid_until' !~ '(Z|[+]00:00)$' THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 started:=(d->>'valid_from')::timestamptz; ended:=(d->>'valid_until')::timestamptz;
 IF ended<=started OR ended>started+interval '31 days' OR jsonb_typeof(d->'rates') IS DISTINCT FROM 'array'
 OR jsonb_array_length(d->'rates') NOT BETWEEN 1 AND 6 THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 names:=ARRAY[]::text[];
 FOR rate IN SELECT value FROM jsonb_array_elements(d->'rates') LOOP
  IF jsonb_typeof(rate) IS DISTINCT FROM 'object' OR NOT(rate ?& ARRAY['measure','amount','per'])
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(rate) k WHERE k NOT IN ('measure','amount','per'))
  OR jsonb_typeof(rate->'measure') IS DISTINCT FROM 'string' OR jsonb_typeof(rate->'amount') IS DISTINCT FROM 'string'
  OR rate->>'measure' NOT IN ('bfl_credit','input_tokens','output_tokens','cached_input_tokens','cache_write_tokens','thinking_tokens')
  OR rate->>'amount' !~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,8})?$'
  OR jsonb_typeof(rate->'per') IS DISTINCT FROM 'number' OR rate->>'per' !~ '^[0-9]+$'
  OR (rate->>'per')::numeric NOT BETWEEN 1 AND 1000000000 OR rate->>'measure'=ANY(names)
  OR (d->>'provider'='bfl' AND rate->>'measure'<>'bfl_credit') OR (d->>'provider'='gemini' AND rate->>'measure'='bfl_credit')
  THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
  names:=array_append(names,rate->>'measure');
 END LOOP;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO existing FROM ops.supplier_price_versions WHERE operation_id=p_operation;
 IF FOUND THEN
  IF existing.canonical_document IS DISTINCT FROM p_canonical OR existing.evidence_bucket IS DISTINCT FROM p_bucket
  OR existing.evidence_object_key IS DISTINCT FROM p_key OR existing.reason IS DISTINCT FROM p_reason
  THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN existing.id;
 END IF;
 INSERT INTO ops.supplier_price_versions(canonical_document,document,document_sha256,provider,model,endpoint_origin,merchant_fingerprint,billing_group,version,valid_from,valid_until,evidence_bucket,evidence_object_key,evidence_storage_version_id,evidence_sha256,actor,reason,operation_id)
 VALUES(p_canonical,d,p_sha,d->>'provider',d->>'model',d->>'endpoint_origin',d->>'merchant_fingerprint',d->>'billing_group',d->>'version',started,ended,p_bucket,p_key,p_storage_version,d->>'evidence_sha256',session_user,p_reason,p_operation) RETURNING id INTO identity;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'supplier_price_publish','supplier_price',identity,p_reason,d->>'evidence_sha256',jsonb_build_object('document_sha256',p_sha,'evidence_kind',d->>'evidence_kind','actual_spending_verified',false),p_operation);
 RETURN identity;
END $$;
ALTER FUNCTION ops.publish_supplier_price(text,text,text,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.publish_supplier_price(text,text,text,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.publish_supplier_price(text,text,text,text,text,uuid,text) TO ops_admin;

CREATE FUNCTION ops.supplier_price_context(p_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',id,'document',document,'document_sha256',document_sha256,'evidence_archived',true,'actual_spending_verified',false)
 FROM ops.supplier_price_versions WHERE id=p_id
$$;
ALTER FUNCTION ops.supplier_price_context(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.supplier_price_context(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.supplier_price_context(uuid) TO ops_admin;

-- A public client cannot select a price; trusted quote construction freezes its scope.
CREATE FUNCTION app.validate_supplier_price_scope() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE execution jsonb; entry record; p ops.supplier_price_versions; account jsonb; expected_endpoint text;
BEGIN
 execution:=NEW.input_snapshot->'_execution';
 IF (execution ? 'provider_price_versions') IS DISTINCT FROM true THEN RETURN NEW; END IF;
 IF execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
 OR jsonb_typeof(execution->'provider_price_versions') IS DISTINCT FROM 'object'
 OR execution->'provider_price_versions'='{}'::jsonb THEN RAISE EXCEPTION 'invalid_price_scope'; END IF;
 FOR entry IN SELECT * FROM jsonb_each(execution->'provider_price_versions') LOOP
  IF entry.key NOT IN ('generate','detect') OR jsonb_typeof(entry.value) IS DISTINCT FROM 'object'
  OR NOT(entry.value ?& ARRAY['id','document_sha256'])
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(entry.value) k WHERE k NOT IN ('id','document_sha256'))
  OR jsonb_typeof(entry.value->'id') IS DISTINCT FROM 'string'
  OR jsonb_typeof(entry.value->'document_sha256') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_price_scope'; END IF;
  SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(entry.value->>'id')::uuid AND document_sha256=entry.value->>'document_sha256';
  IF NOT FOUND OR now()<p.valid_from OR now()>=p.valid_until
  OR p.provider<>(CASE WHEN entry.key='generate' THEN 'bfl' ELSE 'gemini' END) THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
  account:=execution->'provider_accounts'->entry.key;
  IF jsonb_typeof(account) IS DISTINCT FROM 'object' OR NOT(account ?& ARRAY['merchant_fingerprint','billing_group'])
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(account) k WHERE k NOT IN ('merchant_fingerprint','billing_group'))
  OR jsonb_typeof(account->'merchant_fingerprint') IS DISTINCT FROM 'string' OR jsonb_typeof(account->'billing_group') IS DISTINCT FROM 'string'
  OR account->>'merchant_fingerprint' IS DISTINCT FROM p.merchant_fingerprint
  OR account->>'billing_group' IS DISTINCT FROM p.billing_group THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
  expected_endpoint:=p.endpoint_origin||CASE WHEN entry.key='generate' THEN '/v1/flux-3-image' ELSE '/v1beta/models/gemini-3.8-flash:generateContent' END;
  IF execution->'provider_endpoints'->>entry.key IS DISTINCT FROM expected_endpoint THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
ALTER FUNCTION app.validate_supplier_price_scope() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.validate_supplier_price_scope() FROM PUBLIC;
CREATE TRIGGER quote_supplier_price_scope BEFORE INSERT ON app.quotes FOR EACH ROW EXECUTE FUNCTION app.validate_supplier_price_scope();
