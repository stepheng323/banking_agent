# Oracle Cloud Infrastructure (Terraform)

This directory contains the "Infrastructure as Code" (IaC) to automatically deploy the Banking Agent server on Oracle Cloud Free Tier.

## 🏗️ The Analogy: Building a House

Think of this code as a set of blueprints for a house. Instead of manually laying bricks (clicking buttons in the console), we give these blueprints to a robot (Terraform) to build it for us.

### 1. `variables.tf` (The Specs)

This defines the _inputs_ we need. Like:

- "What is the owner's name?" (Your Oracle OCID)
- "How big should the house be?" (Instance Shape)
- "Do you have the keys?" (SSH Public Key)

### 2. `network.tf` (The Land & Driveway)

Before we build the server, we need a place to put it.

- **VCN (Virtual Cloud Network)**: The plot of land.
- **Subnet**: The specific address on the street.
- **Security List (Firewall)**: The fence and gate.
  - We explicitly open **Port 8000** (Gate A) so WhatsApp can talk to the bot.
  - We open **Port 22** (Gate B) so you can SSH in.

### 3. `compute.tf` (The House)

This builds the actual server (Virtual Machine).

- **Resource**: `oci_core_instance`
- **Shape**: We ask for the **Ampere A1** shape (4 CPUs, 24GB RAM) because it's free and powerful.
- **Image**: We tell it to install **Ubuntu 22.04**.

### 4. `userdata.sh` (The Interior Decorator)

This is a script that runs **automatically** the very first time the server turns on.

- It updates the system.
- It installs **Docker** and **Docker Compose**.
- It configures the internal Linux firewall.
- _Result:_ When you log in, the server is already ready to run your code.

### 5. `outputs.tf` (The Keys & Address)

After Terraform finishes building, it prints these out:

- **Public IP**: The address of your new server.
- **SSH Command**: The exact command to log in.
- **Webhook URL**: The URL to paste into the Meta Developer Portal.

---

## 🚀 How to Deploy

1.  **Install Terraform**:

    - Mac: `brew install terraform`
    - Windows: `choco install terraform`
    - Linux: `sudo apt install terraform`

2.  **Create your config file**:
    Copy `terraform.tfvars.example` to `terraform.tfvars` and fill in your details (OCIDs from Oracle Console).

3.  **Run commands**:

    ```bash
    # Initialize (download plugins)
    terraform init

    # Preview what will happen
    terraform plan

    # Build it!
    terraform apply
    ```
