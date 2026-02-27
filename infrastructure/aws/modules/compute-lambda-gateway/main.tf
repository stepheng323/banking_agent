resource "aws_iam_role" "gateway_lambda_role" {
  name = "${var.project_name}-gateway-lambda-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "gateway_lambda_basic_execution" {
  role       = aws_iam_role.gateway_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "gateway_lambda_publish_policy" {
  name = "${var.project_name}-gateway-lambda-sns-${var.environment}"
  role = aws_iam_role.gateway_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = values(var.topic_arns)
      }
    ]
  })
}

resource "aws_lambda_function" "gateway" {
  function_name = "${var.project_name}-gateway-webhooks-${var.environment}"
  role          = aws_iam_role.gateway_lambda_role.arn
  package_type  = "Image"
  image_uri     = var.gateway_lambda_image_url
  timeout       = 30
  memory_size   = 1024
  publish       = true

  image_config {
    command = ["apps.gateway.lambda_handler.handler"]
  }

  environment {
    variables = {
      APP_ENV        = var.environment
      ENVIRONMENT    = var.environment
      PROJECT_NAME   = var.project_name
      AWS_REGION     = var.aws_region
      AWS_ACCOUNT_ID = var.aws_account_id
      DATABASE_URL   = var.database_url
      REDIS_URL      = var.redis_url
    }
  }
}
