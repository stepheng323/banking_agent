# Oracle Cloud Free Tier Deployment Guide

## Overview

Deploy your banking agent to Oracle Cloud's **Always Free** tier - completely free forever.

**What you'll get:**

- 4 ARM cores (Ampere A1)
- 24GB RAM
- 200GB boot volume
- 10TB outbound/month

---

## Step 0: Get Your Credentials

You need to setup the secure OCI Config File.

1.  **Install OCI CLI**:
    `bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"`
2.  **Run Setup**:
    Run `oci setup config` in your terminal. It will ask for:
    - **User OCID**: From your Profile page.
    - **Tenancy OCID**: From your Tenancy page.
    - **Region**: `us-ashburn-1` (or your region).
    - **Generate key pair?**: Choose `Y`.
3.  **Upload Key**:

    - The tool will generate a public key.
    - Go to **User Profile** -> **API Keys** -> **Add API Key**.
    - Select **Paste Public Key** and paste the output from the terminal.

    _Now Terraform can deploy without you ever touching a private key file!_

4.  **Compartment OCID**:
    - Usually the same as your **Tenancy OCID** (Root Compartment).

---

## Step 1: Create a Virtual Machine

1. **Go to Oracle Cloud Console**: [cloud.oracle.com](https://cloud.oracle.com)

2. **Navigate**: Menu (☰) → Compute → Instances → **Create Instance**

3. **Configure the instance:**

   | Setting       | Value                                                       |
   | ------------- | ----------------------------------------------------------- |
   | **Name**      | `banking-agent`                                             |
   | **Placement** | Keep default (AD-1 or available)                            |
   | **Image**     | Ubuntu 22.04 (click "Change image")                         |
   | **Shape**     | Click "Change shape" → **Ampere** → **VM.Standard.A1.Flex** |
   | **OCPUs**     | 4 (free tier max)                                           |
   | **Memory**    | 24 GB (free tier max)                                       |

4. **Networking:**

   - Create new VCN or use existing
   - Assign public IPv4 address: **Yes**

5. **SSH Keys:**

   - Generate or upload your SSH public key
   - Save the private key if generated!

6. Click **Create** → Wait for instance to be RUNNING (2-3 min)

---

## Step 2: Configure Firewall (Security List)

Oracle blocks ports by default. Open the ports you need:

1. **Go to**: Menu → Networking → Virtual Cloud Networks
2. Click your VCN → **Security Lists** → Default Security List
3. **Add Ingress Rules:**

   | Source CIDR | Protocol | Port | Description                |
   | ----------- | -------- | ---- | -------------------------- |
   | `0.0.0.0/0` | TCP      | 8000 | Gateway (WhatsApp webhook) |
   | `0.0.0.0/0` | TCP      | 22   | SSH (already exists)       |

---

## Step 3: SSH Into Your Instance

```bash
# Get Public IP from instance details page
ssh -i /path/to/your/private_key ubuntu@<PUBLIC_IP>
```

---

## Step 4: Install Docker

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sudo sh

# Add user to docker group
sudo usermod -aG docker ubuntu

# Logout and login again for group change
exit
```

SSH back in, then verify:

```bash
docker --version
```

---

## Step 5: Install Docker Compose

```bash
# Install docker-compose plugin
sudo apt install docker-compose-plugin -y

# Verify
docker compose version
```

---

## Step 6: Clone Your Repository

```bash
# Clone your repo
git clone https://github.com/YOUR_USERNAME/banking_agent.git
cd banking_agent
```

---

## Step 7: Create Production Environment File

```bash
nano .env
```

Paste your production environment variables:

```bash
# WhatsApp / Meta
META_VERIFY_TOKEN=your_webhook_verify_token
META_ACCESS_TOKEN=EAAxxxxxxx
META_PHONE_NUMBER_ID=123456789

# OpenAI
OPENAI_API_KEY=sk-xxxxxxx

# Banking Provider
MONO_API_KEY=live_xxxxxxx

# Database (use docker network)
DATABASE_URL=postgresql://banking_user:banking_pass@db:5432/banking_db

# Redis (use docker network)
REDIS_URL=redis://redis:6379

# WhatsApp Flows (if using)
ONBOARDING_FLOW_ID=your_flow_id
ACCOUNT_LINKING_FLOW_ID=your_flow_id
PIN_CONFIRMATION_FLOW_ID=your_flow_id
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`

---

## Step 8: Create Production docker-compose.yml

```bash
nano docker-compose.yml
```

```yaml
version: "3.8"

services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: banking_db
      POSTGRES_USER: banking_user
      POSTGRES_PASSWORD: banking_pass
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U banking_user -d banking_db"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  gateway:
    build:
      context: .
      dockerfile: apps/gateway/Dockerfile
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  core:
    build:
      context: .
      dockerfile: apps/core/Dockerfile
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  receipt-worker:
    build:
      context: .
      dockerfile: apps/receipt_worker/Dockerfile
    env_file: .env
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

---

## Step 9: Build and Start Services

```bash
# Build and start (first time takes 5-10 min)
docker compose -f docker-compose.yml up -d --build

# Check status
docker compose -f docker-compose.yml ps

# View logs
docker compose -f docker-compose.yml logs -f
```

---

## Step 10: Configure WhatsApp Webhook

In your Meta Developer Console, set the webhook URL to:

```
https://<YOUR_ORACLE_PUBLIC_IP>:8000/webhook
```

> ⚠️ For production, you'll want HTTPS. You can use:
>
> - Cloudflare Tunnel (free, easiest)
> - Let's Encrypt + nginx reverse proxy
> - AWS CloudFront or similar CDN

---

## Quick Reference Commands

```bash
# Start services
docker compose -f docker-compose.yml up -d

# Stop services
docker compose -f docker-compose.yml down

# View logs
docker compose -f docker-compose.yml logs -f gateway

# Restart a service
docker compose -f docker-compose.yml restart gateway

# Rebuild after code changes
git pull
docker compose -f docker-compose.yml up -d --build

# Check resource usage
docker stats
```

---

## Troubleshooting

| Issue                       | Solution                                       |
| --------------------------- | ---------------------------------------------- |
| Can't connect to port 8000  | Check Oracle Security List ingress rules       |
| Container keeps restarting  | Check logs: `docker compose logs <service>`    |
| Out of memory               | Unlikely with 24GB, but check `docker stats`   |
| Database connection refused | Wait for db healthcheck, or check DATABASE_URL |

---

## Next Steps

1. **HTTPS**: Set up Cloudflare Tunnel or nginx + Let's Encrypt
2. **Domain**: Point your domain to the Oracle public IP
3. **Monitoring**: Add Prometheus/Grafana or use Oracle's built-in monitoring
4. **Backups**: Set up PostgreSQL backups to Oracle Object Storage (also free)
