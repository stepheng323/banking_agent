variable "tenancy_ocid" {
  description = "Oracle Cloud Tenancy OCID"
  type        = string
}

variable "user_ocid" {
  description = "Oracle Cloud User OCID"
  type        = string
}

variable "fingerprint" {
  description = "Oracle Cloud API Key Fingerprint"
  type        = string
}

variable "private_key_path" {
  description = "Path to the private key used for Oracle Cloud API authentication"
  type        = string
}

variable "region" {
  description = "Oracle Cloud Region (e.g., us-ashburn-1)"
  type        = string
  default     = "us-ashburn-1"
}

variable "compartment_ocid" {
  description = "OCID of the compartment where resources will be created"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH Public Key to access the instance"
  type        = string
}

variable "instance_shape" {
  description = "Instance Shape"
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  description = "Number of OCPUs"
  type        = number
  default     = 4
}

variable "instance_memory_in_gbs" {
  description = "Memory in GBs"
  type        = number
  default     = 24
}
