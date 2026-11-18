-- Reviewed fixture evidence only. This table is not a settlement ledger.
CREATE TABLE ops.supplier_bill_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, task_id uuid NOT NULL, attempt_id uuid NOT NULL UNIQUE,
 receipt_id uuid NOT NULL, provider_id text NOT NULL, response_digest text NOT NULL,
 provider text NOT NULL, model text NOT NULL, endpoint_origin text NOT NULL, merchant_fingerprint text NOT NULL, billing_group text NOT NULL,
 provider_request_id text NOT NULL, statement_fingerprint text NOT NULL, line_fingerprint text NOT NULL,
 currency text NOT NULL CHECK(currency IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT')),
 amount numeric(20,8) NOT NULL CHECK(amount>=0),
 document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'), canonical_document text NOT NULL CHECK(octet_length(canonical_document)<=65536), document jsonb NOT NULL,
 context_sha256 text NOT NULL CHECK(context_sha256 ~ '^[0-9a-f]{64}$'), canonical_context text NOT NULL CHECK(octet_length(canonical_context)<=65536), context jsonb NOT NULL,
 evidence_bucket text NOT NULL, evidence_object_key text NOT NULL, evidence_storage_version_id text,
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 review_status text NOT NULL DEFAULT 'offline_fixture_matched' CHECK(review_status='offline_fixture_matched'),
 actual_spending_verified boolean NOT NULL DEFAULT false CHECK(actual_spending_verified=false),
 step_ceiling_relation text NOT NULL CHECK(step_ceiling_relation IN ('within_ceiling','over_ceiling')),
 actor text NOT NULL, reason text NOT NULL, operation_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_id,task_id,attempt_id,receipt_id,provider_id,response_digest)
 REFERENCES ops.provider_receipts(user_id,task_id,attempt_id,id,provider_id,response_digest),
 UNIQUE(provider,endpoint_origin,merchant_fingerprint,billing_group,provider_request_id),
 UNIQUE(endpoint_origin,merchant_fingerprint,statement_fingerprint,line_fingerprint)
);
ALTER TABLE ops.supplier_bill_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.supplier_bill_reviews FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.supplier_bill_reviews FROM PUBLIC;
GRANT SELECT,INSERT ON ops.supplier_bill_reviews TO app_executor;
GRANT SELECT ON ops.supplier_bill_reviews TO ops_admin;
CREATE POLICY bill_review_executor ON ops.supplier_bill_reviews TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY bill_review_operator ON ops.supplier_bill_reviews FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_bill_review BEFORE UPDATE OR DELETE ON ops.supplier_bill_reviews FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION ops.archive_supplier_bill(p_document text,p_sha text,p_context text,p_context_sha text,p_bucket text,p_key text,p_version text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; ctx jsonb; supplied jsonb; field text; u uuid; task uuid; attempt uuid; receipt ops.provider_receipts;
 prior ops.supplier_bill_reviews; identity uuid; relation text;
BEGIN
 IF p_document IS NULL OR octet_length(p_document)>65536 OR p_context IS NULL OR octet_length(p_context)>65536
 OR p_sha IS NULL OR encode(sha256(convert_to(p_document,'UTF8')),'hex') IS DISTINCT FROM p_sha
 OR p_context_sha IS NULL OR encode(sha256(convert_to(p_context,'UTF8')),'hex') IS DISTINCT FROM p_context_sha
 OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_bucket IS NULL OR length(p_bucket) NOT BETWEEN 1 AND 255 OR p_key IS NULL OR p_key !~ '^private/supplier-bill/[0-9a-f-]{36}/[0-9a-f]{64}$'
 THEN RAISE EXCEPTION 'invalid_bill_archive'; END IF;
 d:=p_document::jsonb; supplied:=p_context::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR jsonb_typeof(supplied) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_bill_archive'; END IF;
 IF NOT(d ?& ARRAY['schema_version','task_id','attempt_id','quote_input_sha256','provider','model','endpoint_origin','merchant_fingerprint','billing_group','provider_request_id','response_digest','currency','amount','evidence_kind','evidence_sha256','statement_fingerprint','line_fingerprint'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(d) key WHERE key NOT IN ('schema_version','task_id','attempt_id','quote_input_sha256','provider','model','endpoint_origin','merchant_fingerprint','billing_group','provider_request_id','response_digest','currency','amount','evidence_kind','evidence_sha256','statement_fingerprint','line_fingerprint'))
 THEN RAISE EXCEPTION 'invalid_bill_archive'; END IF;
 FOREACH field IN ARRAY ARRAY['task_id','attempt_id','quote_input_sha256','provider','model','endpoint_origin','merchant_fingerprint','billing_group','provider_request_id','response_digest','currency','amount','evidence_kind','evidence_sha256','statement_fingerprint','line_fingerprint'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_bill_archive'; END IF;
 END LOOP;
 IF jsonb_typeof(d->'schema_version') IS DISTINCT FROM 'number' OR d->>'schema_version' IS DISTINCT FROM '1'
 OR d->>'amount' !~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,8})?$' OR d->>'evidence_kind' IS DISTINCT FROM 'offline_fixture'
 OR d->>'evidence_sha256' !~ '^[0-9a-f]{64}$' OR d->>'statement_fingerprint' !~ '^[0-9a-f]{64}$'
 OR d->>'line_fingerprint' !~ '^[0-9a-f]{64}$' OR p_key IS DISTINCT FROM 'private/supplier-bill/'||p_operation::text||'/'||(d->>'evidence_sha256')
 THEN RAISE EXCEPTION 'invalid_bill_archive'; END IF;
 task:=(d->>'task_id')::uuid; attempt:=(d->>'attempt_id')::uuid;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO prior FROM ops.supplier_bill_reviews WHERE operation_id=p_operation;
 IF FOUND THEN
  IF prior.document_sha256 IS DISTINCT FROM p_sha OR prior.context_sha256 IS DISTINCT FROM p_context_sha
  OR prior.reason IS DISTINCT FROM p_reason OR prior.evidence_bucket IS DISTINCT FROM p_bucket OR prior.evidence_object_key IS DISTINCT FROM p_key
  THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN prior.id;
 END IF;
 ctx:=ops.supplier_billing_context(task,attempt);
 IF ctx IS DISTINCT FROM supplied THEN RAISE EXCEPTION 'bill_context_changed'; END IF;
 IF ctx->>'profile' IS DISTINCT FROM 'offline_protocol_mock' OR ctx->'receipt_applied' IS DISTINCT FROM 'true'::jsonb
 OR ctx->'price_scope_verified' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'bill_scope_unverified'; END IF;
 FOREACH field IN ARRAY ARRAY['task_id','attempt_id','quote_input_sha256','provider','model','endpoint_origin','merchant_fingerprint','billing_group','provider_request_id','response_digest','currency'] LOOP
  IF jsonb_typeof(ctx->'scope'->field) IS DISTINCT FROM 'string' OR ctx->'scope'->>field IS DISTINCT FROM d->>field THEN RAISE EXCEPTION 'bill_scope_mismatch'; END IF;
 END LOOP;
 IF d->>'provider'='gemini' AND d->>'provider_request_id'='gemini-'||attempt::text THEN RAISE EXCEPTION 'local_gemini_id_is_not_supplier_billing_id'; END IF;
 SELECT * INTO receipt FROM ops.provider_receipts WHERE task_id=task AND user_id=u AND attempt_id=attempt AND response_digest=d->>'response_digest' AND provider_id=(CASE WHEN d->>'provider'='gemini' THEN 'gemini-'||attempt::text ELSE d->>'provider_request_id' END) ORDER BY created_at,id LIMIT 1;
 IF NOT FOUND THEN RAISE EXCEPTION 'bill_receipt_required'; END IF;
 relation:=CASE WHEN (d->>'amount')::numeric>(ctx->>'step_ceiling')::numeric THEN 'over_ceiling' ELSE 'within_ceiling' END;
 INSERT INTO ops.supplier_bill_reviews(user_id,task_id,attempt_id,receipt_id,provider_id,response_digest,provider,model,endpoint_origin,merchant_fingerprint,billing_group,provider_request_id,statement_fingerprint,line_fingerprint,currency,amount,document_sha256,canonical_document,document,context_sha256,canonical_context,context,evidence_bucket,evidence_object_key,evidence_storage_version_id,evidence_sha256,step_ceiling_relation,actor,reason,operation_id)
 VALUES(u,task,attempt,receipt.id,receipt.provider_id,receipt.response_digest,d->>'provider',d->>'model',d->>'endpoint_origin',d->>'merchant_fingerprint',d->>'billing_group',d->>'provider_request_id',d->>'statement_fingerprint',d->>'line_fingerprint',d->>'currency',(d->>'amount')::numeric,p_sha,p_document,d,p_context_sha,p_context,ctx,p_bucket,p_key,p_version,d->>'evidence_sha256',relation,session_user,p_reason,p_operation) RETURNING id INTO identity;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'supplier_bill_fixture_archived','task',task,p_reason,d->>'evidence_sha256',jsonb_build_object('review_id',identity,'attempt_id',attempt,'document_sha256',p_sha,'context_sha256',p_context_sha,'review_status','offline_fixture_matched','actual_spending_verified',false,'step_ceiling_relation',relation),p_operation);
 RETURN identity;
END $$;
ALTER FUNCTION ops.archive_supplier_bill(text,text,text,text,text,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.archive_supplier_bill(text,text,text,text,text,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.archive_supplier_bill(text,text,text,text,text,text,text,uuid,text) TO ops_admin;

CREATE FUNCTION ops.supplier_bill_review(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE saved ops.supplier_bill_reviews;
BEGIN
 SELECT * INTO saved FROM ops.supplier_bill_reviews WHERE id=p_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('id',saved.id,'task_id',saved.task_id,'attempt_id',saved.attempt_id,'document_sha256',saved.document_sha256,
 'context_sha256',saved.context_sha256,'evidence_sha256',saved.evidence_sha256,'amount',saved.amount::text,'currency',saved.currency,
 'review_status',saved.review_status,'step_ceiling_relation',saved.step_ceiling_relation,'actual_spending_verified',false);
END $$;
ALTER FUNCTION ops.supplier_bill_review(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.supplier_bill_review(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.supplier_bill_review(uuid) TO ops_admin;
