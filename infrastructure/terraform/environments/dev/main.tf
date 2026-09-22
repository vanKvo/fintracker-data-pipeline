data "aws_caller_identity" "current" {}

locals {
  common_tags = {
    Project     = "fintracker"
    Service     = "data-pipeline"
    Environment = var.env_name
    ManagedBy   = "terraform"
  }

  name = "FinTracker-DataPipeline-${var.env_name}"

  shared_env = {
    APP_ENV = var.env_name
  }
}

# One shared deployment artifact for every function, mirroring the old CDK stack's single
# Code.from_asset(".") reused across all seven Lambdas, differing only by handler.
data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = var.lambda_source_dir
  output_path = "${path.module}/build/lambda_package.zip"
}

# Three tables, not one — each repository module (statement_ingestion, categorizer, gatekeeper's
# mapping_repository) already addresses a distinct table via its own env var
# (JOB_TRACKER_TABLE / MERCHANT_REGISTRY_TABLE / BANK_MAPPING_TABLE), so this mirrors the app's
# actual data model instead of a single table standing in for three. On-demand billing means this
# costs the same in aggregate request units/storage as one table would — the split buys capacity
# isolation (a MerchantRegistry hot key can't throttle job-status writes) and tighter per-function
# IAM scoping (below) for free, not at a price.
module "job_tracker_table" {
  source     = "../../modules/dynamodb_table"
  table_name = var.job_tracker_table_name
  enable_ttl = true # update_job_status sets a 7-day "ttl" on every row
  tags       = local.common_tags
}

module "merchant_registry_table" {
  source     = "../../modules/dynamodb_table"
  table_name = var.merchant_registry_table_name
  tags       = local.common_tags
}

module "bank_mapping_table" {
  source     = "../../modules/dynamodb_table"
  table_name = var.bank_mapping_table_name
  tags       = local.common_tags
}

# --- Step Function task Lambdas ---------------------------------------------------------------

module "gatekeeper_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-Gatekeeper"
  handler                  = "src.gatekeeper.handler.gatekeeper_handler"
  memory_size              = 512
  timeout                  = 120
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  environment_variables = merge(local.shared_env, {
    MAX_STATEMENT_FILE_SIZE_BYTES = tostring(var.max_statement_file_size_bytes)
    MAX_STATEMENT_PDF_PAGES       = tostring(var.max_statement_pdf_pages)
    JOB_TRACKER_TABLE             = module.job_tracker_table.table_name
    BANK_MAPPING_TABLE            = module.bank_mapping_table.table_name
  })

  extra_policy_statements = [
    {
      sid       = "ReadUploadedStatement"
      actions   = ["s3:GetObject"]
      resources = ["${module.statement_bucket.bucket_arn}/*"]
    },
    {
      # gatekeeper_handler.py only ever writes job status (update_job_status -> PutItem); it
      # never reads a job back.
      sid       = "WriteJobStatus"
      actions   = ["dynamodb:PutItem"]
      resources = [module.job_tracker_table.table_arn]
    },
    {
      # run_gatekeeper proposes a column mapping by reading the bank's known variants
      # (get_bank_mapping -> GetItem); confirming/correcting a mapping is a different Lambda
      # (mapping_confirmation), which is the only one that writes here.
      sid       = "ReadBankMapping"
      actions   = ["dynamodb:GetItem"]
      resources = [module.bank_mapping_table.table_arn]
    },
    {
      # Resolving a waitForTaskToken task — wildcard because the task token itself scopes the
      # call, not the state machine ARN (same reasoning the CDK stack used).
      sid       = "ResumePausedExecution"
      actions   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
      resources = ["*"]
    },
  ]
}

module "extractor_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-Extractor"
  handler                  = "src.extractor.handler.ingestion_handler"
  memory_size              = 1024
  timeout                  = 300
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  environment_variables = merge(local.shared_env, {
    JOB_TRACKER_TABLE = module.job_tracker_table.table_name
  })

  extra_policy_statements = [
    {
      sid       = "ReadWriteStatementObjects"
      actions   = ["s3:GetObject", "s3:PutObject"]
      resources = ["${module.statement_bucket.bucket_arn}/*"]
    },
    {
      # Textract's AnalyzeDocument has no resource-level ARN scoping (documented AWS limitation,
      # REQ-DP-07 B. Constraints) — this wildcard is the ceiling of what IAM allows, not a
      # broader grant than the CDK stack had.
      sid       = "AnalyzeDocument"
      actions   = ["textract:AnalyzeDocument"]
      resources = ["*"]
    },
    {
      # ingestion_handler.py only ever writes job status (update_job_status -> PutItem).
      sid       = "WriteJobStatus"
      actions   = ["dynamodb:PutItem"]
      resources = [module.job_tracker_table.table_arn]
    },
  ]
}

module "normalizer_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-Normalizer"
  handler                  = "src.normalizer.handler.normalizer_handler"
  memory_size              = 512
  timeout                  = 180
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  environment_variables = merge(local.shared_env, {
    JOB_TRACKER_TABLE       = module.job_tracker_table.table_name
    MERCHANT_REGISTRY_TABLE = module.merchant_registry_table.table_name
  })

  extra_policy_statements = [
    {
      # lookup_merchant (GetItem) on a cache hit; cache_merchant (PutItem) after a Comprehend
      # fallback classification, to avoid re-classifying the same merchant next time.
      sid       = "MerchantRegistryAccess"
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
      resources = [module.merchant_registry_table.table_arn]
    },
    {
      # normalizer_handler.py only ever writes job status (update_job_status -> PutItem).
      sid       = "WriteJobStatus"
      actions   = ["dynamodb:PutItem"]
      resources = [module.job_tracker_table.table_arn]
    },
    {
      # Comprehend custom classifiers DO support resource-level scoping (REQ-DP-07 fix) — scoped
      # to this account/region's endpoint, not a blanket wildcard.
      sid       = "ClassifyMerchant"
      actions   = ["comprehend:ClassifyDocument"]
      resources = ["arn:aws:comprehend:${var.aws_region}:${data.aws_caller_identity.current.account_id}:document-classifier-endpoint/fintracker-merchant-classifier"]
    },
  ]
}

module "ledger_push_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-LedgerPush"
  handler                  = "src.data_dispatcher.handler.ledger_push_handler"
  memory_size              = 256
  timeout                  = 300
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  # Dev only, per this task's scope: INTERNAL_API_KEY passed straight from tfvars, not SSM
  # SecureString — core/config.py::get_param() reads the plain env var when present. Production
  # must not follow this pattern (CLAUDE.md's Python Standards: "Secrets via AWS SSM Parameter
  # Store, never hardcoded").
  environment_variables = merge(local.shared_env, {
    LEDGER_API_URL    = var.ledger_api_url
    INTERNAL_API_KEY  = var.internal_api_key
    JOB_TRACKER_TABLE = module.job_tracker_table.table_name
  })

  extra_policy_statements = [
    {
      # ledger_push_handler.py only ever writes job status (update_job_status -> PutItem).
      sid       = "WriteJobStatus"
      actions   = ["dynamodb:PutItem"]
      resources = [module.job_tracker_table.table_arn]
    },
  ]
}

# --- Orchestration --------------------------------------------------------------------------

module "pipeline_state_machine" {
  source                 = "../../modules/step_functions_pipeline"
  state_machine_name     = local.name
  gatekeeper_lambda_arn  = module.gatekeeper_lambda.function_arn
  extractor_lambda_arn   = module.extractor_lambda.function_arn
  normalizer_lambda_arn  = module.normalizer_lambda.function_arn
  ledger_push_lambda_arn = module.ledger_push_lambda.function_arn
  log_retention_days     = var.log_retention_days
  tags                   = local.common_tags
}

module "s3_processor_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-S3Processor"
  handler                  = "src.statement_ingestion.handler.s3_processor_handler"
  memory_size              = 256
  timeout                  = 30
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  environment_variables = merge(local.shared_env, {
    STATE_MACHINE_ARN = module.pipeline_state_machine.state_machine_arn
    JOB_TRACKER_TABLE = module.job_tracker_table.table_name
    # REQ-DP-05: looks up the statement's verified owner from the Ledger before trusting
    # anything about who it belongs to — same Ledger reachability caveat as ledger_push_lambda
    # below (a locally-run Ledger needs a tunnel; see the Terraform README).
    LEDGER_API_URL   = var.ledger_api_url
    INTERNAL_API_KEY = var.internal_api_key
  })

  extra_policy_statements = [
    {
      sid       = "ReadUploadMetadata"
      actions   = ["s3:GetObject"]
      resources = ["${module.statement_bucket.bucket_arn}/*"]
    },
    {
      sid       = "StartPipelineExecution"
      actions   = ["states:StartExecution"]
      resources = [module.pipeline_state_machine.state_machine_arn]
    },
    {
      # s3_processor_handler.py only ever writes job status (update_job_status -> PutItem).
      sid       = "WriteInitialJobStatus"
      actions   = ["dynamodb:PutItem"]
      resources = [module.job_tracker_table.table_arn]
    },
  ]
}

module "statement_bucket" {
  source                         = "../../modules/s3_statement_bucket"
  bucket_name                    = var.statements_bucket_name
  processor_lambda_arn           = module.s3_processor_lambda.function_arn
  processor_lambda_function_name = module.s3_processor_lambda.function_name
  cors_allowed_origins           = var.cors_allowed_origins
  tags                           = local.common_tags
}

# --- User-facing API (job polling + mapping confirmation) ------------------------------------

module "status_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-JobStatus"
  handler                  = "src.statement_ingestion.status_handler.job_status_handler"
  memory_size              = 128
  timeout                  = 10
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  environment_variables = merge(local.shared_env, {
    JOB_TRACKER_TABLE = module.job_tracker_table.table_name
  })

  extra_policy_statements = [
    {
      # job_status_handler.py only ever reads (get_job -> GetItem); it never writes.
      sid       = "ReadJobStatus"
      actions   = ["dynamodb:GetItem"]
      resources = [module.job_tracker_table.table_arn]
    },
  ]
}

module "mapping_confirmation_lambda" {
  source                   = "../../modules/lambda_function"
  function_name            = "${local.name}-MappingConfirmation"
  handler                  = "src.gatekeeper.mapping_confirmation_handler.mapping_confirmation_handler"
  memory_size              = 256
  timeout                  = 30
  package_filename         = data.archive_file.lambda_package.output_path
  package_source_code_hash = data.archive_file.lambda_package.output_base64sha256
  log_retention_days       = var.log_retention_days
  tags                     = local.common_tags

  environment_variables = merge(local.shared_env, {
    JOB_TRACKER_TABLE  = module.job_tracker_table.table_name
    BANK_MAPPING_TABLE = module.bank_mapping_table.table_name
  })

  extra_policy_statements = [
    {
      sid       = "ResumePausedExecution"
      actions   = ["states:SendTaskSuccess"]
      resources = ["*"]
    },
    {
      # confirm_column_mapping reads the job (get_job -> GetItem) then records the new status
      # (update_job_status -> PutItem).
      sid       = "JobTableAccess"
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
      resources = [module.job_tracker_table.table_arn]
    },
    {
      # save_bank_mapping_correction reads the existing mapping (get_bank_mapping -> GetItem)
      # before writing the merged correction back (PutItem).
      sid       = "BankMappingTableAccess"
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
      resources = [module.bank_mapping_table.table_arn]
    },
  ]
}

module "http_api" {
  source                                    = "../../modules/http_api"
  api_name                                  = "${local.name}-Api"
  aws_region                                = var.aws_region
  cognito_user_pool_id                      = var.cognito_user_pool_id
  cognito_user_pool_client_id               = var.cognito_user_pool_client_id
  status_lambda_arn                         = module.status_lambda.invoke_arn
  status_lambda_function_name               = module.status_lambda.function_name
  mapping_confirmation_lambda_arn           = module.mapping_confirmation_lambda.invoke_arn
  mapping_confirmation_lambda_function_name = module.mapping_confirmation_lambda.function_name
  tags                                      = local.common_tags
}
