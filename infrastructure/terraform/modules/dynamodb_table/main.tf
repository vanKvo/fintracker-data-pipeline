# Generic PK/SK table — instantiated once per repository module (job tracker, merchant
# registry, bank mapping; see environments/dev/main.tf), each addressed by its own env var
# (JOB_TRACKER_TABLE / MERCHANT_REGISTRY_TABLE / BANK_MAPPING_TABLE). Three separate resources,
# not three logical partitions of one table — see environments/dev/main.tf's comment on why, and
# the note in the previous CLAUDE.md-adjacent discussion: on-demand billing means this costs the
# same in aggregate as a single table would.
resource "aws_dynamodb_table" "this" {
  name         = var.table_name
  billing_mode = var.billing_mode
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = var.enable_ttl
  }

  tags = var.tags
}
