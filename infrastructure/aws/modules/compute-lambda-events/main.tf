locals {
  workers = {
    "transaction-worker" = {
      handler = "apps.transaction.lambda_handler.handler"
      timeout = 120
      memory  = 1024
    }
    "receipt-worker" = {
      handler = "apps.receipt.lambda_handler.handler"
      timeout = 90
      memory  = 2048
    }
    "scheduler-dispatcher" = {
      handler = "apps.chat.src.lambda_handlers.scheduler_dispatcher_handler.handler"
      timeout = 60
      memory  = 1024
    }
  }

  queue_worker_map = {
    "transactions" = "transaction-worker"
    "receipts"     = "receipt-worker"
  }

  async_event_mappings = {
    for queue_key, worker_name in local.queue_worker_map : queue_key => {
      worker_name  = worker_name
      queue_arn    = var.queue_arns[queue_key]
      batch_size   = lookup(var.batch_size_by_queue, queue_key, 5)
      batch_window = lookup(var.batch_window_by_queue, queue_key, 1)
      max_conc     = lookup(var.max_concurrency_by_queue, queue_key, null)
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

resource "aws_iam_role_policy" "lambda_sns_publish" {
  name = "${var.project_name}-lambda-workers-sns-${var.environment}"
  role = aws_iam_role.lambda_worker_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [var.sns_topic_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_ssm_policy" {
  name = "${var.project_name}-lambda-workers-ssm-${var.environment}"
  role = aws_iam_role.lambda_worker_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = values(var.all_parameter_arns)
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = var.ssm_kms_key_arn
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "workers" {
  for_each = local.workers

  name              = "/aws/lambda/${var.project_name}-${each.key}-${var.environment}"
  retention_in_days = var.log_retention_in_days
}

resource "aws_lambda_function" "workers" {
  for_each = local.workers

  function_name                  = "${var.project_name}-${each.key}-${var.environment}"
  role                           = aws_iam_role.lambda_worker_role.arn
  package_type                   = "Image"
  image_uri                      = lookup(var.worker_lambda_image_urls, each.key, var.worker_lambda_image_urls["transaction-worker"])
  timeout                        = each.value.timeout
  memory_size                    = each.value.memory
  publish                        = true
  reserved_concurrent_executions = lookup(var.reserved_concurrency_by_worker, each.key, null)

  image_config {
    command = [each.value.handler]
  }

  environment {
    variables = {
      for key, value in merge(
        {
          APP_ENV        = var.environment
          ENVIRONMENT    = var.environment
          PROJECT_NAME   = var.project_name
          AWS_ACCOUNT_ID = var.aws_account_id
        },
        var.non_secret_env_vars,
        var.secret_env_vars
      ) : key => value if key != "AWS_REGION"
    }
  }

  depends_on = [aws_cloudwatch_log_group.workers]
}

resource "aws_cloudwatch_event_rule" "schedule_dispatcher" {
  count = var.enable_schedule_dispatcher ? 1 : 0

  name                = "${var.project_name}-scheduler-dispatch-${var.environment}"
  schedule_expression = var.schedule_dispatch_expression
}

resource "aws_cloudwatch_event_target" "schedule_dispatcher" {
  count = var.enable_schedule_dispatcher ? 1 : 0

  rule      = aws_cloudwatch_event_rule.schedule_dispatcher[0].name
  target_id = "scheduler-dispatcher"
  arn       = aws_lambda_function.workers["scheduler-dispatcher"].arn
}

resource "aws_lambda_permission" "allow_eventbridge_schedule_dispatcher" {
  count = var.enable_schedule_dispatcher ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridgeSchedulerDispatcher"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.workers["scheduler-dispatcher"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule_dispatcher[0].arn
}

resource "aws_lambda_event_source_mapping" "async_queue_mappings" {
  for_each = local.async_event_mappings

  event_source_arn                   = each.value.queue_arn
  function_name                      = aws_lambda_function.workers[each.value.worker_name].arn
  enabled                            = true
  batch_size                         = each.value.batch_size
  maximum_batching_window_in_seconds = each.value.batch_window
  function_response_types            = ["ReportBatchItemFailures"]

  dynamic "scaling_config" {
    for_each = each.value.max_conc == null ? [] : [each.value.max_conc]
    content {
      maximum_concurrency = scaling_config.value
    }
  }
}
