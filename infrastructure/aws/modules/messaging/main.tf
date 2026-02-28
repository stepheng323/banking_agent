resource "aws_sns_topic" "topics" {
  for_each = toset(var.topics)
  name     = "${var.project_name}-${each.value}-${var.environment}"

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

resource "aws_sns_topic_subscription" "subscriptions" {
  for_each = var.queues

  topic_arn            = aws_sns_topic.topics[each.key].arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.queues[each.key].arn
  raw_message_delivery = true
}

# Allow SNS to publish to SQS
resource "aws_sqs_queue_policy" "sns_to_sqs" {
  for_each = var.queues

  queue_url = aws_sqs_queue.queues[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.queues[each.key].arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.topics[each.key].arn
          }
        }
      }
    ]
  })
}
