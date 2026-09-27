# --- Provider / environment ---------------------------------------------------------------

variable "aws_region" {
  type = string
}

variable "aws_profile" {
  description = "Named AWS CLI profile to deploy with. Leave null to use the default credential chain (env vars, SSO, etc)."
  type        = string
  default     = null
}

variable "env_name" {
  description = "Short environment tag used in resource names (e.g. \"dev\")."
  type        = string
  default     = "dev"
}

# --- App config -----------------------------------------------------------------------------
# REQ from this task: dev doesn't provision SSM Parameter Store / Secrets Manager — every value
# the application needs is declared here and passed straight through as plain Lambda environment
# variables. core/config.py's get_param() checks the environment first and only falls back to SSM
# when a variable isn't set that way, so this works without any application code path change in
# how the Lambdas read their config, only in where the value actually comes from in dev.

variable "statements_bucket_name" {
  description = "Must match the Ledger's STATEMENTS_BUCKET_NAME for this environment — same bucket, presigned PUT target on one side, S3-trigger source on the other."
  type        = string
}

# Three tables, not one — each repository module already addresses a distinct table via its own
# env var (see the corresponding modules in main.tf and their per-function IAM scoping).
variable "job_tracker_table_name" {
  type    = string
  default = "FinTracker_JobTracker_Dev"
}

variable "bank_mapping_table_name" {
  type    = string
  default = "FinTracker_BankMapping_Dev"
}

variable "cognito_user_pool_id" {
  description = "Shared Cognito User Pool ID (see the User Profile service's identity stack) that this API's JWT authorizer validates against."
  type        = string
}

variable "cognito_user_pool_client_id" {
  type = string
}

variable "ledger_api_url" {
  description = "Base URL the Data Dispatcher pushes normalized transactions to. NOTE: the Ledger normally runs locally via `mvn spring-boot:run` / docker-compose (see the root CLAUDE.md) — a real AWS Lambda cannot reach `localhost`. Point this at a tunnel (ngrok/Cloudflare Tunnel) to your local Ledger, or a real deployed dev Ledger, not localhost."
  type        = string
}

variable "max_statement_file_size_bytes" {
  type    = number
  default = 26214400 # 25 MiB, REQ-DP-04
}

variable "max_statement_pdf_pages" {
  type    = number
  default = 50
}

variable "cors_allowed_origins" {
  description = "Origins allowed to PUT directly to the statements bucket (the Angular dev server by default)."
  type        = list(string)
  default     = ["http://localhost:4200"]
}

# --- Deployment artifact ----------------------------------------------------------------------

variable "lambda_source_dir" {
  description = "Directory to zip as the Lambda deployment package for every function in this service — run the packaging step from the service README first: `pip install -t package/ .` (plus copying src/ in) from the service root (services/fintracker-data-pipeline/), so this directory actually contains the installed dependencies alongside src/. Relative to this environment's own directory (environments/dev/), four levels up to the service root."
  type        = string
  default     = "../../../../package"
}

variable "log_retention_days" {
  type    = number
  default = 14
}
