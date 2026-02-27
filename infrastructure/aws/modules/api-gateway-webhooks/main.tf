locals {
  webhook_routes = toset([
    "GET /webhook",
    "POST /webhook/whatsapp",
    "POST /webhook/flow",
    "POST /webhook/telegram",
    "POST /webhook/mono",
  ])
}

resource "aws_apigatewayv2_api" "webhooks" {
  name          = "${var.project_name}-webhooks-${var.environment}"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "gateway_lambda" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook_routes" {
  for_each = local.webhook_routes

  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.gateway_lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.webhooks.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "allow_apigw_invoke" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/*"
}
