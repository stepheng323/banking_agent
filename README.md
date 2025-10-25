# Banking Agent - WhatsApp Banking Chatbot

A Python monorepo for a WhatsApp-based banking agent with LLM processing and automatic receipt generation.

## 🏗️ Architecture

This is a **Python monorepo** managed with **Pants**, containing two services:

1. **Gateway Service** (`apps/gateway/`) - WhatsApp webhook ingestion + LangGraph LLM agent
2. **Core Service** (`apps/core/`) - Banking operations + Receipt generation
3. **Shared Library** (`shared/`) - Common models, utilities, and clients

## 📋 Prerequisites

- Python 3.12
- Pants (installed automatically via bootstrap script)
- Docker & Docker Compose (for local development)
- PostgreSQL 16 (via Docker)
- Redis 7 (via Docker)

## 🚀 Quick Start

### 1. Clone and Install

```bash
# Clone the repository
cd /home/abiodun/dev/banking_agent

# Pants will bootstrap automatically on first use
# Generate lock file (first time only)
make setup
```

### 2. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit .env with your credentials
# - META_VERIFY_TOKEN
# - META_ACCESS_TOKEN
# - OPENAI_API_KEY
# - AWS credentials
```

### 3. Run with Docker Compose (Recommended)

```bash
# Start all services (gateway, core, db, redis)
make docker-up

# View logs
make docker-logs

# Stop services
make docker-down
```

Services will be available at:

- **Gateway Service**: http://localhost:8000
- **Core Service**: http://localhost:8001
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379

### 4. Run Locally (Development)

```bash
# Terminal 1 - Start database and redis
docker-compose up db redis

# Terminal 2 - Run Gateway service
make dev-gateway

# Terminal 3 - Run Core service
make dev-core
```

## 🛠️ Development Commands

```bash
# Show all available commands
make help

# Run tests
make test

# Format code
make format

# Lint code
make lint

# Run format, lint, and tests
make check

# List all Pants targets
make list

# Clean Pants cache
make clean
```

## 📦 Pants Commands

```bash
# List all targets in the repo
pants list ::

# Run a specific service
pants run apps/gateway:bin
pants run apps/core:bin

# Run tests for a specific service
pants test apps/gateway::
pants test apps/core::

# Format specific files
pants fmt apps/gateway/main.py

# Package as PEX binary
pants package apps/gateway:bin
pants package apps/core:bin
```

## 🏢 Project Structure

```
banking_agent/
├── apps/
│   ├── gateway/                  # WhatsApp Gateway Service
│   │   ├── main.py              # FastAPI entry point
│   │   ├── api/                 # API routes (webhook)
│   │   ├── adapters/            # WhatsApp, LLM adapters
│   │   ├── core/                # Business logic
│   │   ├── BUILD                # Pants build config
│   │   ├── Dockerfile           # Container image
│   │   └── requirements.txt     # Service dependencies
│   │
│   └── core/                     # Core Banking Service
│       ├── src/
│       │   └── main.py          # FastAPI entry point
│       ├── BUILD                # Pants build config
│       ├── Dockerfile           # Container image
│       └── requirements.txt     # Service dependencies
│
├── shared/                       # Shared library
│   ├── __init__.py
│   ├── models/                  # Pydantic models
│   ├── config/                  # Configuration
│   ├── clients/                 # AWS, DB clients
│   ├── utils/                   # Utilities
│   └── BUILD                    # Pants build config
│
├── pants.toml                    # Pants configuration
├── BUILD                         # Root build file
├── requirement.txt               # Project dependencies
├── python-default.lock           # Pants lock file
├── docker-compose.yml            # Docker orchestration
├── Makefile                      # Development commands
└── README.md                     # This file
```

## 🔄 Service Communication

```
WhatsApp → Gateway Service → Core Service → Banking API
              ↓                    ↓
          LangGraph             Receipt Gen
              ↓                    ↓
           Redis ←────────────────┘
              ↓
         PostgreSQL
```

## 🧪 Testing

```bash
# Run all tests
pants test ::

# Run tests for specific service
pants test apps/gateway::
pants test apps/core::

# Run with coverage
pants test --coverage ::
```

## 📝 Adding Dependencies

1. Add dependency to `requirement.txt`
2. Regenerate lock file:
   ```bash
   pants generate-lockfiles --resolve=python-default
   ```

## 🐳 Docker Deployment

### Build Images

```bash
# Build all services
make docker-build

# Or manually
docker-compose build
```

### Deploy to AWS

1. **Push images to ECR**:

   ```bash
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ecr-url>
   docker tag banking_agent-gateway:latest <ecr-url>/gateway:latest
   docker push <ecr-url>/gateway:latest
   ```

2. **Deploy to ECS/App Runner** (configure via AWS Console or Terraform)

## 🔧 Troubleshooting

### Pants Issues

```bash
# Clean cache and restart
pants clean-all
rm -rf .pants.d

# Regenerate lock file
pants generate-lockfiles --resolve=python-default
```

### DNS Issues in WSL2

If you get "Could not resolve host: github.com":

```bash
# Fix DNS
sudo sh -c 'echo "nameserver 8.8.8.8" > /etc/resolv.conf'
sudo sh -c 'echo "nameserver 1.1.1.1" >> /etc/resolv.conf'
```

### Docker Issues

```bash
# Reset Docker
make docker-down
docker system prune -af
make docker-up
```

## 📚 Tech Stack

- **Build System**: Pants
- **Language**: Python 3.12
- **Web Framework**: FastAPI
- **LLM Framework**: LangChain + LangGraph
- **Database**: PostgreSQL 16
- **Cache/Queue**: Redis 7
- **Cloud**: AWS (S3, SES, RDS, etc.)
- **Containerization**: Docker

## 🤝 Contributing

1. Create a feature branch
2. Make changes
3. Run checks: `make check`
4. Create pull request

## 📄 License

MIT License
