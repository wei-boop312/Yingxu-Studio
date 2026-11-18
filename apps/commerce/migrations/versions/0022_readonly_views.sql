-- Invoker views preserve the authenticated server's transaction-local tenant context.
-- No table-wide SELECT, auth/ops access, storage keys, prompts or provider payloads.
CREATE VIEW app.readonly_users WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,display_name,status,created_at FROM app.users;
GRANT SELECT(id,display_name,status,created_at) ON app.users TO app_readonly;
CREATE VIEW app.readonly_products WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,name,category,revision,archived_at,created_at,updated_at FROM app.products;
GRANT SELECT(id,user_id,name,category,revision,archived_at,created_at,updated_at) ON app.products TO app_readonly;
CREATE VIEW app.readonly_appearances WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,product_id,name,revision,archived_at,created_at,updated_at FROM app.appearance_profiles;
GRANT SELECT(id,user_id,product_id,name,revision,archived_at,created_at,updated_at) ON app.appearance_profiles TO app_readonly;
CREATE VIEW app.readonly_assets WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,product_id,kind,status,current_version_id,created_at FROM app.assets;
GRANT SELECT(id,user_id,product_id,kind,status,current_version_id,created_at) ON app.assets TO app_readonly;
CREATE VIEW app.readonly_asset_versions WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,product_id,asset_id,version_number,sha256,mime,width,height,byte_size,color_space,canonical,source_version_id,created_at FROM app.asset_versions;
GRANT SELECT(id,user_id,product_id,asset_id,version_number,sha256,mime,width,height,byte_size,color_space,canonical,source_version_id,created_at) ON app.asset_versions TO app_readonly;
CREATE VIEW app.readonly_tasks WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,product_id,source_asset_version_id,status,phase,credits_status,reserved_credits,final_version_id,compliance_id,revision,created_at,updated_at,completed_at,error_code FROM app.generation_tasks;
GRANT SELECT(id,user_id,product_id,source_asset_version_id,status,phase,credits_status,reserved_credits,final_version_id,compliance_id,revision,created_at,updated_at,completed_at,error_code) ON app.generation_tasks TO app_readonly;
CREATE VIEW app.readonly_wallets WITH (security_invoker=true,security_barrier=true) AS
 SELECT user_id,available,reserved,funded_total,spent_total,revision,updated_at FROM app.wallets;
GRANT SELECT(user_id,available,reserved,funded_total,spent_total,revision,updated_at) ON app.wallets TO app_readonly;
CREATE VIEW app.readonly_credits WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,task_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,created_at FROM app.credit_transactions;
GRANT SELECT(id,user_id,task_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,created_at) ON app.credit_transactions TO app_readonly;
CREATE VIEW app.readonly_compliance WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,user_id,product_id,task_id,final_version_id,final_sha256,auto_overall,sampling_selected,created_at FROM app.compliance_checks;
GRANT SELECT(id,user_id,product_id,task_id,final_version_id,final_sha256,auto_overall,sampling_selected,created_at) ON app.compliance_checks TO app_readonly;
CREATE VIEW app.readonly_published_rules WITH (security_invoker=true,security_barrier=true) AS
 SELECT id,platform,marketplace,category,image_type,version,evidence_status,config_sha256,published_at FROM app.platform_rules WHERE published_at IS NOT NULL;
GRANT SELECT(id,platform,marketplace,category,image_type,version,evidence_status,config_sha256,published_at) ON app.platform_rules TO app_readonly;
GRANT SELECT ON app.readonly_users,app.readonly_products,app.readonly_appearances,app.readonly_assets,app.readonly_asset_versions,app.readonly_tasks,app.readonly_wallets,app.readonly_credits,app.readonly_compliance,app.readonly_published_rules TO app_readonly;
