output "topic_arns" {
  description = "Map of topic names to ARNs"
  value       = { for k, v in aws_sns_topic.topics : k => v.arn }
}

output "queue_arns" {
  description = "Map of queue names to ARNs"
  value       = { for k, v in aws_sqs_queue.queues : k => v.arn }
}

output "queue_urls" {
  description = "Map of queue names to URLs"
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
