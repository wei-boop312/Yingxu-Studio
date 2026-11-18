-- Schema 3 is explicit metadata only. Existing quote and schema-4 budget guards still reject it.
CREATE OR REPLACE FUNCTION ops.validated_runtime_binding(p_canonical text,p_sha text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE d jsonb; name text; field text; step jsonb;
BEGIN
 IF p_canonical IS NULL OR octet_length(p_canonical)>16384 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha
 OR NOT(p_canonical IS JSON OBJECT WITH UNIQUE KEYS) THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR NOT(d ?& ARRAY['schema_version','version','generate','detect'])
 OR (SELECT count(*) FROM jsonb_object_keys(d))<>4 OR jsonb_typeof(d->'schema_version') IS DISTINCT FROM 'number' OR d->>'schema_version' NOT IN ('1','2','3')
 OR jsonb_typeof(d->'version') IS DISTINCT FROM 'string' OR d->>'version' !~ '^[A-Za-z0-9_.-]{1,80}$' THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
 FOREACH name IN ARRAY ARRAY['generate','detect'] LOOP
  step:=d->name;
  IF jsonb_typeof(step) IS DISTINCT FROM 'object'
  OR NOT(step ?& ARRAY['provider','model','endpoint_origin','merchant_fingerprint','credential_fingerprint','billing_group'])
  OR (SELECT count(*) FROM jsonb_object_keys(step))<>6 THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  FOREACH field IN ARRAY ARRAY['provider','model','endpoint_origin','merchant_fingerprint','credential_fingerprint','billing_group'] LOOP
   IF jsonb_typeof(step->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  END LOOP;
  IF step->>'merchant_fingerprint' !~ '^[0-9a-f]{64}$' OR step->>'credential_fingerprint' !~ '^[0-9a-f]{64}$'
  OR length(step->>'billing_group') NOT BETWEEN 1 AND 160 OR trim(step->>'billing_group')='' OR step->>'billing_group' ~ '[[:cntrl:]]'
  THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  IF name='generate' THEN
   IF (d->>'schema_version'='1' AND (step->>'provider'<>'bfl' OR step->>'model'<>'flux-3-image' OR step->>'endpoint_origin'<>'https://api.bfl.ai'))
   OR (d->>'schema_version' IN ('2','3') AND (step->>'provider'<>'aliyun_bailian' OR step->>'model'<>'qwen-image-3.0'
   OR step->>'endpoint_origin' !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com$')) THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  ELSIF d->>'schema_version'='3' THEN
   IF step->>'provider'<>'aliyun_bailian_vision' OR step->>'model'<>'qwen3.8-flash'
   OR step->>'endpoint_origin' !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com$'
   THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  ELSE
   IF step->>'model'<>'gemini-3.8-flash' OR step->>'provider' NOT IN ('google_gemini','hubway_gemini')
   OR (step->>'provider'='google_gemini' AND step->>'endpoint_origin'<>'https://generativelanguage.googleapis.com')
   OR (step->>'provider'='hubway_gemini' AND step->>'endpoint_origin' NOT IN ('https://hubway.cc','https://z.hubway.cc')) THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  END IF;
 END LOOP;
 IF d->>'schema_version'='3' THEN
  FOREACH field IN ARRAY ARRAY['endpoint_origin','credential_fingerprint','merchant_fingerprint','billing_group'] LOOP
   IF d->'generate'->>field IS DISTINCT FROM d->'detect'->>field THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  END LOOP;
 END IF;
 RETURN jsonb_build_object('provider_runtime_sha256',p_sha,'provider_models',jsonb_build_object('generate',d->'generate'->>'model','detect',d->'detect'->>'model'),
 'provider_endpoints',jsonb_build_object('generate',(d->'generate'->>'endpoint_origin')||CASE WHEN d->>'schema_version' IN ('2','3') THEN '/api/v1/services/aigc/image-generation/generation' ELSE '/v1/flux-3-image' END,
 'detect',(d->'detect'->>'endpoint_origin')||CASE WHEN d->>'schema_version'='3' THEN '/compatible-mode/v1/chat/completions' ELSE '/v1beta/models/gemini-3.8-flash:generateContent' END),
 'provider_accounts',jsonb_build_object('generate',(d->'generate')-'provider'-'model'-'endpoint_origin'-'credential_fingerprint','detect',(d->'detect')-'provider'-'model'-'endpoint_origin'-'credential_fingerprint'));
END $$;
ALTER FUNCTION ops.validated_runtime_binding(text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.validated_runtime_binding(text,text) FROM PUBLIC;
