variable "state_machine_name" {
  type = string
}

variable "gatekeeper_lambda_arn" {
  type = string
}

variable "extractor_lambda_arn" {
  type = string
}

variable "normalizer_lambda_arn" {
  type = string
}

variable "ledger_push_lambda_arn" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "execution_timeout_seconds" {
  description = "REQ-DP-01 F. Error Handling — MAPPING_CONFIRMATION_TIMEOUT: bounds how long an execution can sit paused at the Gatekeeper's waitForTaskToken step (or anywhere else) before Step Functions force-fails it, so an abandoned mapping-confirmation dialog doesn't hold an execution open forever. 86400s (24h) matches the old CDK stack's `timeout=Duration.hours(24)` on the StateMachine construct, which this Terraform port had been missing entirely (no TimeoutSeconds was ever set)."
  type        = number
  default     = 86400
}

variable "tags" {
  type    = map(string)
  default = {}
}
