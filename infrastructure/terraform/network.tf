resource "oci_core_vcn" "banking_vcn" {
  cidr_block     = "10.0.0.0/16"
  compartment_id = var.compartment_ocid
  display_name   = "banking-vcn"
  dns_label      = "bankingvcn"
}

resource "oci_core_internet_gateway" "banking_ig" {
  compartment_id = var.compartment_ocid
  display_name   = "banking-internet-gateway"
  vcn_id         = oci_core_vcn.banking_vcn.id
}

resource "oci_core_route_table" "banking_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.banking_vcn.id
  display_name   = "banking-route-table"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.banking_ig.id
  }
}

resource "oci_core_security_list" "banking_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.banking_vcn.id
  display_name   = "banking-security-list"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  # SSH
  ingress_security_rules {
    protocol = "6" # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      max = 22
      min = 22
    }
  }

  # Webhook (Gateway) running on port 8000
  ingress_security_rules {
    protocol = "6" # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      max = 8000
      min = 8000
    }
  }
  
  # Core Service (if you want to expose it, e.g. for debugging, port 8001)
  # Uncomment if needed
  # ingress_security_rules {
  #   protocol = "6"
  #   source   = "0.0.0.0/0"
  #   tcp_options {
  #     max = 8001
  #     min = 8001
  #   }
  # }
}

resource "oci_core_subnet" "banking_subnet" {
  cidr_block        = "10.0.1.0/24"
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.banking_vcn.id
  display_name      = "banking-public-subnet"
  dns_label         = "bankingsubnet"
  security_list_ids = [oci_core_security_list.banking_sl.id]
  route_table_id    = oci_core_route_table.banking_rt.id
}
