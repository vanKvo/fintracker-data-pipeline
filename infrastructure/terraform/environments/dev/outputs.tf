output "statements_bucket_name" {
  value = module.statement_bucket.bucket_name
}

output "job_tracker_table_name" {
  value = module.job_tracker_table.table_name
}

output "merchant_registry_table_name" {
  value = module.merchant_registry_table.table_name
}

output "bank_mapping_table_name" {
  value = module.bank_mapping_table.table_name
}

output "state_machine_arn" {
  value = module.pipeline_state_machine.state_machine_arn
}

output "data_pipeline_api_url" {
  description = "Feed this into the Ledger/UI config as the Data Pipeline's polling + mapping-confirmation base URL."
  value       = module.http_api.api_endpoint
}
