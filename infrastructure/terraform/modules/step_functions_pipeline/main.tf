resource "aws_iam_role" "state_machine" {
  name = "${var.state_machine_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "invoke_tasks" {
  name = "${var.state_machine_name}-invoke-tasks"
  role = aws_iam_role.state_machine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "InvokePipelineLambdas"
      Effect = "Allow"
      Action = ["lambda:InvokeFunction"]
      Resource = [
        var.gatekeeper_lambda_arn,
        var.extractor_lambda_arn,
        var.normalizer_lambda_arn,
        var.ledger_push_lambda_arn,
      ]
    }]
  })
}

resource "aws_cloudwatch_log_group" "state_machine" {
  # Step Functions requires this exact "/aws/vendedlogs/states/" prefix for a log group it writes
  # to directly, or CreateStateMachine's logging_configuration rejects the ARN outright.
  name              = "/aws/vendedlogs/states/${var.state_machine_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_iam_role_policy" "logging" {
  name = "${var.state_machine_name}-logging"
  role = aws_iam_role.state_machine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "WriteExecutionLogs"
      Effect = "Allow"
      Action = [
        "logs:CreateLogDelivery",
        "logs:GetLogDelivery",
        "logs:UpdateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:DescribeResourcePolicies",
        "logs:DescribeLogGroups",
      ]
      Resource = "*"
    }]
  })
}

# STANDARD, not EXPRESS: Gatekeeper's user mapping-confirmation pause (waitForTaskToken) can wait
# indefinitely on human input. EXPRESS caps total execution at 5 minutes, which the pause can
# easily exceed — same reasoning the original CDK stack documented.
resource "aws_sfn_state_machine" "pipeline" {
  name     = var.state_machine_name
  role_arn = aws_iam_role.state_machine.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/statemachine.asl.json.tpl", {
    gatekeeper_lambda_arn     = var.gatekeeper_lambda_arn
    extractor_lambda_arn      = var.extractor_lambda_arn
    normalizer_lambda_arn     = var.normalizer_lambda_arn
    ledger_push_lambda_arn    = var.ledger_push_lambda_arn
    execution_timeout_seconds = var.execution_timeout_seconds
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.state_machine.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy.invoke_tasks, aws_iam_role_policy.logging]
}
