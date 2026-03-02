resource "aws_sns_topic" "async_jobs" {
  name = "${var.project_name}-${var.sns_topic_name}-${var.environment}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "dlqs" {
  for_each = var.queues
  name     = "${var.project_name}-${each.value}-dlq-${var.environment}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "queues" {
  for_each = var.queues
  name     = "${var.project_name}-${each.value}-${var.environment}"

  visibility_timeout_seconds = lookup(var.visibility_timeout_by_queue, each.key, var.default_visibility_timeout_seconds)
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlqs[each.key].arn
    maxReceiveCount     = var.dlq_max_receive_count
  })

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

resource "aws_sqs_queue_policy" "allow_sns" {
  for_each  = var.queues
  queue_url = aws_sqs_queue.queues[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "sns.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.queues[each.key].arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.async_jobs.arn
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic_subscription" "queue_subscriptions" {
  for_each  = var.queues
  topic_arn = aws_sns_topic.async_jobs.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.queues[each.key].arn

  raw_message_delivery = true
  filter_policy        = lookup(var.queue_filter_policies, each.key, null)
  filter_policy_scope  = "MessageAttributes"
}
