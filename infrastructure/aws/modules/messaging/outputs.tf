output "sns_topic_arn" {
  description = "ARN of the consolidated async-jobs SNS topic"
  value       = aws_sns_topic.async_jobs.arn
}

output "queue_arns" {
  description = "Map of queue keys to ARNs"
  value       = { for k, v in aws_sqs_queue.queues : k => v.arn }
}

output "queue_urls" {
  description = "Map of queue keys to URLs"
  value       = { for k, v in aws_sqs_queue.queues : k => v.id }
}

output "queue_names" {
  description = "Map of queue keys to queue resource names"
  value       = { for k, v in aws_sqs_queue.queues : k => v.name }
}

output "dlq_arns" {
  description = "Map of DLQ ARNs"
  value       = { for k, v in aws_sqs_queue.dlqs : k => v.arn }
}
