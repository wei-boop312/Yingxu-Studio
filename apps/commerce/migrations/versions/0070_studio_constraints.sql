-- Row locks require UPDATE privilege; immutable SKU records still reject changes.
GRANT UPDATE(id) ON app.studio_skus TO app_api;
CREATE TRIGGER immutable_studio_sku BEFORE UPDATE OR DELETE ON app.studio_skus
 FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE app.studio_reviews ADD CONSTRAINT studio_review_output_identity UNIQUE(user_id,project_id,output_id,id);
ALTER TABLE app.studio_delivery_items ADD CONSTRAINT studio_delivery_review_matches_output
 FOREIGN KEY(user_id,project_id,output_id,review_id) REFERENCES app.studio_reviews(user_id,project_id,output_id,id);
ALTER TABLE app.studio_outputs ADD CONSTRAINT studio_task_product_matches
 FOREIGN KEY(user_id,product_id,task_id) REFERENCES app.generation_tasks(user_id,product_id,id);
CREATE INDEX studio_reference_asset ON app.studio_references(user_id,product_id,asset_version_id);
CREATE INDEX studio_fact_source_reference ON app.studio_fact_sources(user_id,sku_id,reference_id);
CREATE INDEX studio_plan_fact_version ON app.studio_plan_facts(user_id,sku_id,fact_version_id);
CREATE INDEX studio_plan_reference ON app.studio_plan_references(user_id,sku_id,reference_id);
CREATE INDEX studio_output_asset ON app.studio_outputs(user_id,product_id,asset_version_id);
CREATE INDEX studio_output_source ON app.studio_outputs(user_id,product_id,source_asset_version_id);
CREATE INDEX studio_output_task ON app.studio_outputs(user_id,product_id,task_id);
CREATE INDEX studio_delivery_item_output ON app.studio_delivery_items(user_id,project_id,output_id);
CREATE INDEX studio_delivery_item_review ON app.studio_delivery_items(user_id,project_id,output_id,review_id);
