-- Review-only publication: no automatic rates, quote binding or real spending enabled.
CREATE OR REPLACE FUNCTION ops.publish_supplier_price(p_canonical text,p_sha text,p_bucket text,p_key text,p_storage_version text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; rate jsonb; field text; identity uuid; existing ops.supplier_price_versions; names text[]; started timestamptz; ended timestamptz;
BEGIN
 IF p_canonical IS NULL OR octet_length(p_canonical)>16384 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha
 OR NOT(p_canonical IS JSON OBJECT WITH UNIQUE KEYS)
 OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_bucket IS NULL OR length(p_bucket) NOT BETWEEN 1 AND 255 OR p_key IS NULL OR length(p_key) NOT BETWEEN 1 AND 1024 THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object'
 OR NOT(d ?& ARRAY['schema_version','provider','model','endpoint_origin','merchant_fingerprint','billing_group','version','currency','valid_from','valid_until','evidence_kind','evidence_sha256','complete_meter_coverage','rates'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(d) k WHERE k NOT IN ('schema_version','provider','model','endpoint_origin','merchant_fingerprint','billing_group','version','currency','valid_from','valid_until','evidence_kind','evidence_sha256','complete_meter_coverage','rates'))
 OR jsonb_typeof(d->'schema_version') IS DISTINCT FROM 'number' OR d->>'schema_version' NOT IN ('1','2','3') OR d->'complete_meter_coverage' IS DISTINCT FROM 'true'::jsonb
 OR d->>'provider' NOT IN ('bfl','gemini','aliyun_bailian','aliyun_bailian_vision') OR d->>'currency' NOT IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT')
 OR d->>'evidence_kind' NOT IN ('offline_fixture','reviewed_price') THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 FOREACH field IN ARRAY ARRAY['provider','model','endpoint_origin','merchant_fingerprint','billing_group','version','currency','valid_from','valid_until','evidence_kind','evidence_sha256'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 END LOOP;
 IF d->>'merchant_fingerprint' !~ '^[0-9a-f]{64}$' OR d->>'evidence_sha256' !~ '^[0-9a-f]{64}$'
 OR length(d->>'billing_group') NOT BETWEEN 1 AND 160 OR length(d->>'version') NOT BETWEEN 1 AND 160 OR trim(d->>'billing_group')='' OR trim(d->>'version')=''
 OR d->>'billing_group' ~ '[[:cntrl:]]' OR d->>'version' ~ '[[:cntrl:]]'
 OR (d->>'provider'='bfl' AND (d->>'model'<>'flux-3-image' OR d->>'endpoint_origin'<>'https://api.bfl.ai'))
 OR (d->>'provider'='gemini' AND (d->>'model'<>'gemini-3.8-flash' OR d->>'endpoint_origin' NOT IN ('https://generativelanguage.googleapis.com','https://hubway.cc','https://z.hubway.cc')))
 OR (d->>'provider' IN ('bfl','gemini') AND d->>'schema_version'<>'1')
 OR (d->>'provider'='aliyun_bailian' AND (d->>'schema_version'<>'2' OR d->>'model'<>'qwen-image-3.0' OR d->>'currency'<>'CNY'
 OR d->>'endpoint_origin' !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com$'))
 OR (d->>'provider'='aliyun_bailian_vision' AND (d->>'schema_version'<>'3' OR d->>'model'<>'qwen3.8-flash' OR d->>'currency'<>'CNY' OR d->>'endpoint_origin' !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com$'))
 OR d->>'valid_from' !~ '(Z|[+]00:00)$' OR d->>'valid_until' !~ '(Z|[+]00:00)$' THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 started:=(d->>'valid_from')::timestamptz; ended:=(d->>'valid_until')::timestamptz;
 IF ended<=started OR ended>started+interval '31 days' OR jsonb_typeof(d->'rates') IS DISTINCT FROM 'array'
 OR jsonb_array_length(d->'rates') NOT BETWEEN 1 AND 6 THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 names:=ARRAY[]::text[];
 FOR rate IN SELECT value FROM jsonb_array_elements(d->'rates') LOOP
  IF jsonb_typeof(rate) IS DISTINCT FROM 'object' OR NOT(rate ?& ARRAY['measure','amount','per'])
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(rate) k WHERE k NOT IN ('measure','amount','per'))
  OR jsonb_typeof(rate->'measure') IS DISTINCT FROM 'string' OR jsonb_typeof(rate->'amount') IS DISTINCT FROM 'string'
  OR rate->>'measure' NOT IN ('bfl_credit','input_tokens','output_tokens','cached_input_tokens','cache_write_tokens','thinking_tokens','qima_input_1k','qima_input_2k','qima_output_1k','qima_output_2k')
  OR rate->>'amount' !~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,8})?$'
  OR jsonb_typeof(rate->'per') IS DISTINCT FROM 'number' OR rate->>'per' !~ '^[0-9]+$'
  OR (rate->>'per')::numeric NOT BETWEEN 1 AND 1000000000 OR rate->>'measure'=ANY(names)
  OR (d->>'provider'='bfl' AND rate->>'measure'<>'bfl_credit') OR (d->>'provider'='gemini' AND rate->>'measure' NOT IN ('input_tokens','output_tokens','cached_input_tokens','cache_write_tokens','thinking_tokens'))
  OR (d->>'provider'='aliyun_bailian' AND rate->>'measure' NOT IN ('qima_input_1k','qima_input_2k','qima_output_1k','qima_output_2k'))
  OR (d->>'provider'='aliyun_bailian_vision' AND rate->>'measure' NOT IN ('input_tokens','cached_input_tokens','output_tokens'))
  THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
  names:=array_append(names,rate->>'measure');
 END LOOP;
 IF d->>'provider'='aliyun_bailian' AND cardinality(names)<>4 THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
 IF d->>'provider'='aliyun_bailian_vision' AND cardinality(names)<>3 THEN RAISE EXCEPTION 'invalid_supplier_price'; END IF;
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
