-- Request predictions are not supplier observations and never create cost ledger rows.
CREATE FUNCTION ops.qwen_generation_projection(p_user uuid,p_quote uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q app.quotes; p ops.supplier_price_versions; binding jsonb; account jsonb;
 result jsonb; quantities jsonb; rate jsonb; tier text; width numeric; height numeric;
 numerator numeric:=0; denominator numeric:=1; per_units numeric; scaled numeric;
BEGIN
 IF p_user IS NULL OR p_quote IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO q FROM app.quotes WHERE id=p_quote AND user_id=p_user;
 IF NOT FOUND OR q.input_snapshot->'_execution'->'provider_models'->>'generate' IS DISTINCT FROM 'qwen-image-3.0' THEN RETURN NULL; END IF;
 result:=jsonb_build_object('basis','frozen_single_source_request_v1','quote_input_sha256',q.input_sha256,
 'projection_status','unknown','amount',NULL,'currency',NULL,'actual_spending_verified',false,
 'supplier_usage_observed',false,'execution_enabled',false);
 binding:=q.input_snapshot->'_execution'->'provider_price_versions'->'generate';
 IF binding IS NULL THEN RETURN result||jsonb_build_object('reason','unpriced_rehearsal'); END IF;
 SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
 IF NOT FOUND THEN RETURN result||jsonb_build_object('reason','price_not_found'); END IF;
 result:=result||jsonb_build_object('price_id',p.id,'price_document_sha256',p.document_sha256);
 account:=q.input_snapshot->'_execution'->'provider_accounts'->'generate';
 IF p.provider<>'aliyun_bailian' OR p.model<>'qwen-image-3.0' OR p.document->>'currency'<>'CNY'
 OR account->>'merchant_fingerprint' IS DISTINCT FROM p.merchant_fingerprint
 OR account->>'billing_group' IS DISTINCT FROM p.billing_group
 OR q.input_snapshot->'_execution'->'provider_endpoints'->>'generate' IS DISTINCT FROM p.endpoint_origin||'/api/v1/services/aigc/image-generation/generation'
 THEN RETURN result||jsonb_build_object('reason','price_identity_mismatch'); END IF;
 IF clock_timestamp()<p.valid_from OR clock_timestamp()>=p.valid_until THEN RETURN result||jsonb_build_object('reason','price_not_effective'); END IF;
 width:=(q.input_snapshot->'generation'->'output'->>'width')::numeric;
 height:=(q.input_snapshot->'generation'->'output'->>'height')::numeric;
 IF width IS NULL OR height IS NULL OR width<>trunc(width) OR height<>trunc(height)
 OR width NOT BETWEEN 256 AND 6256 OR height NOT BETWEEN 256 AND 6256
 OR width*height NOT BETWEEN 262144 AND 4000000 OR greatest(width,height)>8*least(width,height)
 THEN RETURN result||jsonb_build_object('reason','request_dimensions_invalid'); END IF;
 -- The current Worker request builder supplies exactly one canonical source PNG.
 tier:=CASE WHEN width*height<=2250000 THEN '1k' ELSE '2k' END;
 quantities:=jsonb_build_object('qima_input_1k',CASE WHEN tier='1k' THEN 1 ELSE 0 END,
 'qima_input_2k',CASE WHEN tier='2k' THEN 1 ELSE 0 END,
 'qima_output_1k',CASE WHEN tier='1k' THEN 1 ELSE 0 END,
 'qima_output_2k',CASE WHEN tier='2k' THEN 1 ELSE 0 END);
 FOR rate IN SELECT value FROM jsonb_array_elements(p.document->'rates') LOOP
  per_units:=(rate->>'per')::numeric;
  numerator:=numerator*per_units+(quantities->>(rate->>'measure'))::numeric*(rate->>'amount')::numeric*100000000*denominator;
  denominator:=denominator*per_units;
 END LOOP;
 scaled:=div(numerator+denominator-1,denominator);
 IF scaled>=100000000000000000000 THEN RETURN result||jsonb_build_object('reason','projection_overflow'); END IF;
 RETURN result||jsonb_build_object('projection_status','projected','reason','reviewed_request_prediction_not_invoice',
 'amount',((scaled/100000000)::numeric(20,8))::text,'currency','CNY','quantities',quantities,
 'calculation_version','exact-request-rational-v1');
END $$;
ALTER FUNCTION ops.qwen_generation_projection(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.qwen_generation_projection(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.qwen_generation_projection(uuid,uuid) TO ops_admin;

CREATE FUNCTION ops.guard_qwen_request_ceiling() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ops.provider_budgets; q app.quotes; projection jsonb;
BEGIN
 IF NEW.step<>'generate' THEN RETURN NEW; END IF;
 SELECT * INTO b FROM ops.provider_budgets WHERE id=NEW.authorization_id;
 IF b.schema_version IS DISTINCT FROM 4 THEN RETURN NEW; END IF;
 SELECT * INTO q FROM app.quotes WHERE id=b.quote_id AND user_id=b.user_id;
 -- Legacy unpriced MockTransport rehearsals remain explicitly unpriced.
 IF (q.input_snapshot->'_execution' ? 'provider_price_versions') IS DISTINCT FROM true THEN RETURN NEW; END IF;
 projection:=ops.qwen_generation_projection(b.user_id,b.quote_id);
 IF projection->>'projection_status' IS DISTINCT FROM 'projected'
 OR projection->>'currency' IS DISTINCT FROM NEW.currency THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 IF (projection->>'amount')::numeric>NEW.ceiling THEN RAISE EXCEPTION 'provider_budget_exhausted'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION ops.guard_qwen_request_ceiling() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.guard_qwen_request_ceiling() FROM PUBLIC;
CREATE TRIGGER qwen_request_ceiling_guard BEFORE INSERT ON ops.provider_budget_holds
FOR EACH ROW EXECUTE FUNCTION ops.guard_qwen_request_ceiling();
