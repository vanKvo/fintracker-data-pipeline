resource "aws_s3_bucket" "statements" {
  bucket = var.bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "statements" {
  bucket = aws_s3_bucket.statements.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "statements" {
  bucket = aws_s3_bucket.statements.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Statement PDFs/CSVs/images are financial documents (CLAUDE.md's PII/log-hygiene concern applies
# to data at rest too) — versioning gives a recovery path if the Ledger's overwrite-on-duplicate
# flow (REQ-STMT-05) or an accidental delete removes something still needed.
resource "aws_s3_bucket_versioning" "statements" {
  bucket = aws_s3_bucket.statements.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Required for the direct-to-S3 browser PUT (Step 2 of the upload flow) to work against a real
# bucket — real S3 enforces CORS on cross-origin requests; LocalStack's S3 emulation did not, so
# this had no equivalent before.
resource "aws_s3_bucket_cors_configuration" "statements" {
  bucket = aws_s3_bucket.statements.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_notification" "statement_upload" {
  bucket = aws_s3_bucket.statements.id

  lambda_function {
    lambda_function_arn = var.processor_lambda_arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}

# The permission S3 itself needs to invoke the processor Lambda — separate from the Lambda's own
# execution role, which governs what the function can do once running, not who may invoke it.
resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = var.processor_lambda_function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.statements.arn
}
