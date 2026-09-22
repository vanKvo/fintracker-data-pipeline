variable "function_name" {
  type = string
}

variable "handler" {
  description = "Dotted Lambda handler path, e.g. \"src.gatekeeper.handler.gatekeeper_handler\"."
  type        = string
}

variable "runtime" {
  type    = string
  default = "python3.12"
}

variable "memory_size" {
  type    = number
  default = 256
}

variable "timeout" {
  description = "Seconds."
  type        = number
  default     = 30
}

variable "package_filename" {
  description = "Path to the built deployment zip — the same artifact for every function in this service, mirroring the old CDK stack's shared Code.from_asset('.')."
  type        = string
}

variable "package_source_code_hash" {
  type = string
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "extra_policy_statements" {
  description = "Additional least-privilege IAM statements this function's role needs beyond CloudWatch Logs (e.g. textract:AnalyzeDocument, dynamodb access, states:SendTaskSuccess)."
  type = list(object({
    sid       = string
    actions   = list(string)
    resources = list(string)
  }))
  default = []
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "tags" {
  type    = map(string)
  default = {}
}
