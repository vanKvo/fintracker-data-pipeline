variable "bucket_name" {
  description = "Must match the Ledger's STATEMENTS_BUCKET_NAME — this is the same bucket the Ledger's S3PresignService issues presigned PUT URLs against (see application.yml's aws.s3.statements-bucket). Owned here since the Data Pipeline is the side that reacts to it."
  type        = string
}

variable "processor_lambda_arn" {
  description = "S3 Processor Lambda invoked on every PutObject — starts the Step Functions execution."
  type        = string
}

variable "processor_lambda_function_name" {
  type = string
}

variable "cors_allowed_origins" {
  description = "Origins allowed to PUT directly to this bucket via the Ledger's presigned URL (the browser uploads straight to S3, bypassing the Ledger — CLAUDE.md's async statement flow). LocalStack never enforced this; real S3 does, so this is a genuinely new requirement of the AWS-backed setup, not just a port of existing config."
  type        = list(string)
  default     = ["http://localhost:4200"]
}

variable "tags" {
  type    = map(string)
  default = {}
}
