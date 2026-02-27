locals {
  workers = {
    "transaction-worker" = {
      handler = "apps.core.src.lambda_handlers.transaction_worker_handler.handler"
      timeout = 120
      memory  = 1024
    }
    "messaging-worker" = {
      handler = "apps.core.src.lambda_handlers.messaging_worker_handler.handler"
      timeout = 90
      memory  = 1024
    }
  }

  queue_worker_map = {
    "transaction-execute"     = "transaction-worker"
    "funding-process"         = "transaction-worker"
    "payout-process"          = "transaction-worker"
    "refund-process"          = "transaction-worker"
    "notification-send"       = "messaging-worker"
    "actionable-message-send" = "messaging-worker"
    "receipt-process"         = "messaging-worker"
  }

  async_event_mappings = {
    for queue_key, worker_name in local.queue_worker_map : queue_key => {
      worker_name  = worker_name
      queue_arn    = var.queue_arns[queue_key]
      batch_size   = lookup(var.batch_size_by_queue, queue_key, 5)
      batch_window = lookup(var.batch_window_by_queue, queue_key, 1)
    }
  }
}

resource "aws_iam_role" "lambda_worker_role" {
  name = "${var.project_name}-lambda-workers-${var.environment}"

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

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_worker_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_sqs_policy" {
  name = "${var.project_name}-lambda-workers-sqs-${var.environment}"
  role = aws_iam_role.lambda_worker_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueUrl"
        ]
        Resource = values(var.queue_arns)
      }
    ]
  })
}

resource "aws_lambda_function" "workers" {
  for_each = local.workers

  function_name = "${var.project_name}-${each.key}-${var.environment}"
  role          = aws_iam_role.lambda_worker_role.arn
  package_type  = "Image"
  image_uri     = var.core_lambda_image_url
  timeout       = each.value.timeout
  memory_size   = each.value.memory
  publish       = true

  image_config {
    command = [each.value.handler]
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

resource "aws_lambda_event_source_mapping" "async_queue_mappings" {
  for_each = local.async_event_mappings

  event_source_arn                   = each.value.queue_arn
  function_name                      = aws_lambda_function.workers[each.value.worker_name].arn
  enabled                            = true
  batch_size                         = each.value.batch_size
  maximum_batching_window_in_seconds = each.value.batch_window
  function_response_types            = ["ReportBatchItemFailures"]
}
