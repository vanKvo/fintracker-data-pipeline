output "bucket_name" {
  value = aws_s3_bucket.statements.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.statements.arn
}
