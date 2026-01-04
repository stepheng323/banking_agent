terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 4.0.0"
    }
  }
  required_version = ">= 1.0.0"
}

provider "oci" {
  config_file_profile = "DEFAULT"
  region              = var.region
}
