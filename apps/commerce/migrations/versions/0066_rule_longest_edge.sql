-- Technical rule constraints are configuration, not a blanket marketplace approval.
CREATE OR REPLACE FUNCTION app.valid_rule_config(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v jsonb;
BEGIN
 IF jsonb_typeof(p) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
 FOR k,v IN SELECT * FROM jsonb_each(p) LOOP
  IF k IN ('min_width','min_height','max_bytes') THEN
   IF v <> 'null'::jsonb AND (jsonb_typeof(v)<>'number' OR v::text !~ '^[1-9][0-9]*$') THEN RETURN false; END IF;
  ELSIF k IN ('max_width','max_height','min_longest_edge','max_longest_edge') THEN
   IF v='null'::jsonb THEN CONTINUE; END IF;
   IF jsonb_typeof(v) IS DISTINCT FROM 'number' OR v::text !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
   IF (v::text)::numeric>1000000 THEN RETURN false; END IF;
  ELSIF k='required_aspect_ratio' THEN
   IF v='null'::jsonb THEN CONTINUE; END IF;
   IF jsonb_typeof(v) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
   IF NOT (v ?& ARRAY['width','height']) OR (SELECT count(*) FROM jsonb_object_keys(v))<>2 THEN RETURN false; END IF;
   IF jsonb_typeof(v->'width') IS DISTINCT FROM 'number' OR jsonb_typeof(v->'height') IS DISTINCT FROM 'number'
   OR v->>'width' !~ '^[1-9][0-9]*$' OR v->>'height' !~ '^[1-9][0-9]*$' THEN RETURN false; END IF;
   IF (v->>'width')::numeric>10000 OR (v->>'height')::numeric>10000 THEN RETURN false; END IF;
  ELSIF k IN ('min_product_long_edge_ratio','minimum_confidence') THEN
   IF v='null'::jsonb AND k='min_product_long_edge_ratio' THEN CONTINUE; END IF;
   IF jsonb_typeof(v)<>'number' OR (v::text)::numeric<0 OR (v::text)::numeric>1 THEN RETURN false; END IF;
  ELSIF k IN ('require_white_background','forbid_added_text','forbid_added_watermarks','allow_on_product_marks','require_identity_review','complete_rule_set') THEN
   IF jsonb_typeof(v)<>'boolean' THEN RETURN false; END IF;
  ELSE RETURN false;
  END IF;
 END LOOP;
 IF (p->>'min_width')::numeric>(p->>'max_width')::numeric OR (p->>'min_height')::numeric>(p->>'max_height')::numeric OR (p->>'min_longest_edge')::numeric>(p->>'max_longest_edge')::numeric THEN RETURN false; END IF;
 RETURN true;
END $$;
CREATE OR REPLACE FUNCTION app.rule_local_constraints_pass(p_rules jsonb,p_width integer,p_height integer,p_bytes bigint) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE rule jsonb; cfg jsonb; ratio jsonb;
BEGIN
 IF p_width IS NULL OR p_height IS NULL OR p_bytes IS NULL OR p_width<=0 OR p_height<=0 OR p_bytes<=0
 OR jsonb_typeof(p_rules) IS DISTINCT FROM 'array' OR jsonb_array_length(p_rules)=0 THEN RETURN false; END IF;
 FOR rule IN SELECT value FROM jsonb_array_elements(p_rules) LOOP
  cfg:=rule->'config';
  IF NOT app.valid_rule_config(cfg) THEN RETURN false; END IF;
  IF p_width<(cfg->>'min_width')::numeric OR p_height<(cfg->>'min_height')::numeric
  OR p_width>(cfg->>'max_width')::numeric OR p_height>(cfg->>'max_height')::numeric
  OR greatest(p_width,p_height)<(cfg->>'min_longest_edge')::numeric OR greatest(p_width,p_height)>(cfg->>'max_longest_edge')::numeric
  OR p_bytes>(cfg->>'max_bytes')::numeric THEN RETURN false; END IF;
  ratio:=cfg->'required_aspect_ratio';
  IF ratio IS NOT NULL AND ratio<>'null'::jsonb AND p_width::bigint*(ratio->>'height')::bigint<>p_height::bigint*(ratio->>'width')::bigint THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;
