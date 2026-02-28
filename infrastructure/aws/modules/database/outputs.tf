output "db_endpoint" {
  description = "The connection endpoint for the RDS instance"
  value       = aws_db_instance.postgres.endpoint
}

output "db_name" {
  description = "The database name"
  value       = aws_db_instance.postgres.db_name
}

output "db_username" {
  description = "The database master username"
  value       = aws_db_instance.postgres.username
}

output "db_password" {
  description = "The database master password"
  value       = var.db_password
  sensitive   = true
}
