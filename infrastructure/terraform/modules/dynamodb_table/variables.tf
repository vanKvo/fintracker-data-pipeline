variable "table_name" {
  description = "Name of the DynamoDB table (single-table design — job tracker, merchant registry, and bank column mappings all live here, keyed by PK/SK)."
  type        = string
}

variable "billing_mode" {
  description = "PAY_PER_REQUEST is the right default for dev/low-traffic — no capacity to size or pay for when idle."
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "enable_ttl" {
  description = "Only the job tracker table's rows carry a \"ttl\" attribute (update_job_status sets a 7-day expiry) — the merchant registry and bank mapping tables never set it, so TTL has nothing to act on there. Off by default; the job tracker instantiation turns it on explicitly."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
