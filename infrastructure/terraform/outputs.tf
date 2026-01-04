output "public_ip" {
  value       = oci_core_instance.banking_server.public_ip
  description = "The Public IP address of the Banking Agent Server"
}

output "ssh_connection_string" {
  value       = "ssh -i <path_to_private_key> ubuntu@${oci_core_instance.banking_server.public_ip}"
  description = "Connection string to access the instance"
}

output "webhook_url" {
  value       = "http://${oci_core_instance.banking_server.public_ip}:8000/webhook"
  description = "The Webhook URL to configure in WhatsApp Cloud API"
}
