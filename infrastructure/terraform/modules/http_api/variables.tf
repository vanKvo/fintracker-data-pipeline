variable "api_name" {
  type = string
}

variable "cognito_user_pool_id" {
  description = "The shared Cognito User Pool every service's API Gateway trusts (CLAUDE.md's request-flow section) — not created here, passed in from whichever stack owns identity infra."
  type        = string
}

variable "cognito_user_pool_client_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "status_lambda_arn" {
  type = string
}

variable "status_lambda_function_name" {
  type = string
}

variable "csv_col_mapping_confirmation_lambda_arn" {
  type = string
}

variable "csv_col_mapping_confirmation_lambda_function_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
