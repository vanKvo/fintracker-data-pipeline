# HTTP API (v2), not REST API (v1) — cheaper, and this surface is two simple routes with a JWT
# authorizer, matching the original CDK stack's choice.
resource "aws_apigatewayv2_api" "this" {
  name          = var.api_name
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.this.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "CognitoAuthorizer"

  jwt_configuration {
    audience = [var.cognito_user_pool_client_id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${var.cognito_user_pool_id}"
  }
}

# GET /jobs/{jobId} — job status polling.
resource "aws_apigatewayv2_integration" "status" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.status_lambda_arn
  payload_format_version = "2.0"

  # Mirrors the CDK stack's ParameterMapping: the JWT's `sub` claim becomes the
  # X-Internal-User-Id header every handler trusts (REQ-DP-05) — never a client-supplied value.
  request_parameters = {
    "overwrite:header.X-Internal-User-Id" = "$context.authorizer.jwt.claims.sub"
  }
}

resource "aws_apigatewayv2_route" "status" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "GET /jobs/{jobId}"
  target             = "integrations/${aws_apigatewayv2_integration.status.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "status_invoke" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.status_lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

# POST /jobs/{jobId}/mapping-confirmation — resumes the paused Step Functions execution.
resource "aws_apigatewayv2_integration" "mapping_confirmation" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.mapping_confirmation_lambda_arn
  payload_format_version = "2.0"

  request_parameters = {
    "overwrite:header.X-Internal-User-Id" = "$context.authorizer.jwt.claims.sub"
  }
}

resource "aws_apigatewayv2_route" "mapping_confirmation" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "POST /jobs/{jobId}/mapping-confirmation"
  target             = "integrations/${aws_apigatewayv2_integration.mapping_confirmation.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_lambda_permission" "mapping_confirmation_invoke" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.mapping_confirmation_lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

# Dev-appropriate: auto_deploy means every route change goes live on apply with no separate
# `aws_apigatewayv2_deployment` step to manage.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags
}
