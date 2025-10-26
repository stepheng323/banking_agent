# Banking Agent - WhatsApp Banking Chatbot

A Python monorepo for a WhatsApp-based banking agent with LLM processing and automatic receipt generation, built with Pants.

## 🏗️ Architecture

**Python Monorepo** managed with **Pants** build system:

- **Gateway Service** (`apps/gateway/`) - WhatsApp webhook + LangGraph LLM agent
- **Core Service** (`apps/core/`) - Banking operations + Receipt generation
- **Shared Library** (`shared/`) - Common models, utilities, and clients

## 🚀 Quick Start

### 1. Setup

```bash
# Generate dependency lock file
make setup

# Configure environment variables
cp .env.example .env
# Edit .env with your WhatsApp and OpenAI credentials
```

### 2. Run Services

**Option A: Docker (Recommended)**

```bash
make docker-up        # Start all services
make docker-logs      # View logs
make docker-down      # Stop services
```

**Option B: Local Development**

```bash
make dev-gateway      # Gateway on port 8000
make dev-core         # Core on port 8001
```

**Services:**

- Gateway: http://localhost:8000
- Core: http://localhost:8001
- PostgreSQL: localhost:5432
- Redis: localhost:6379

## 🛠️ Development

### Common Commands

```bash
make help             # Show all commands
make format           # Format code with Black
make test             # Run tests
make check            # Format, lint, and test
make clean            # Clean Pants cache
```

### Pants Commands

```bash
# List targets
pants list ::

# Run services
pants run apps/gateway:bin
pants run apps/core:bin

# Package as PEX binaries
pants package apps/gateway:bin
pants package apps/core:bin

# Run tests
pants test ::
pants test apps/gateway::
```

## 📁 Project Structure

```
banking_agent/
├── apps/
│   ├── gateway/              # WhatsApp + LangGraph
│   │   ├── main.py
│   │   ├── api/             # FastAPI routes
│   │   ├── adapters/        # WhatsApp, sender
│   │   ├── core/            # Config
│   │   └── BUILD
│   └── core/                # Banking + Receipts
│       ├── src/
│       │   └── main.py
│       └── BUILD
├── shared/                  # Shared library
│   ├── models/
│   ├── config/
│   ├── clients/
│   └── utils/
├── pants.toml              # Pants config
├── requirement.txt         # Dependencies
├── python-default.lock     # Lock file
├── docker-compose.yml      # Docker setup
└── Makefile               # Dev commands
```

## 🔄 Data Flow

```
WhatsApp → Gateway (Port 8000) → LangGraph Agent
              ↓
         Redis Queue
              ↓
    Core Service (Port 8001) → Banking API
              ↓
       Receipt Generation
              ↓
         PostgreSQL
```

## 📦 Managing Dependencies

```bash
# 1. Add to requirement.txt
echo "new-package==1.0.0" >> requirement.txt

# 2. Regenerate lock file
pants generate-lockfiles --resolve=python-default

# 3. Export for IDE (optional)
pants export --resolve=python-default
```

## 🐳 Docker Deployment

### Build & Deploy

```bash
# Build images
make docker-build

# Push to AWS ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <ecr-url>
docker tag banking_agent-gateway:latest <ecr-url>/gateway:latest
docker push <ecr-url>/gateway:latest
```

### Run PEX Binaries

```bash
# Package services
pants package ::

# Run standalone binaries
./dist/apps.gateway/bin.pex
./dist/apps.core/bin.pex
```

## 🧪 Testing

```bash
# All tests
pants test ::

# Specific service
pants test apps/gateway::

# With coverage
pants test --coverage ::
```

## 🔧 Troubleshooting

### Pants Cache Issues

```bash
pants clean-all
rm -rf .pants.d
pants generate-lockfiles --resolve=python-default
```

### WSL2 DNS Issues

```bash
sudo sh -c 'echo "nameserver 8.8.8.8" > /etc/resolv.conf'
sudo sh -c 'echo "nameserver 1.1.1.1" >> /etc/resolv.conf'
```

### WhatsApp 401 Errors

Your `META_ACCESS_TOKEN` may be expired:

1. Go to https://developers.facebook.com/
2. Select your app → WhatsApp → API Setup
3. Generate new access token
4. Update `.env` and restart service

### IDE Setup

```bash
# Export virtualenv for IDE
pants export --resolve=python-default

# Point IDE to:
# dist/export/python/virtualenvs/python-default/3.12.3/bin/python
```

## 📚 Tech Stack

- **Build System**: Pants 2.29
- **Language**: Python 3.12
- **Web**: FastAPI + Uvicorn
- **LLM**: LangChain + LangGraph + OpenAI
- **Database**: PostgreSQL 16
- **Cache**: Redis 7
- **Cloud**: AWS (S3, SES, RDS)
- **Container**: Docker + Docker Compose

## 🎯 Key Features

- ✅ **Monorepo**: Single codebase, independent services
- ✅ **Type Safe**: Pydantic models throughout
- ✅ **Fast Builds**: Pants caching and dependency inference
- ✅ **Production Ready**: PEX binaries or Docker images
- ✅ **LLM Agent**: LangGraph for conversational banking
- ✅ **Auto Receipts**: Automatic receipt generation
- ✅ **Scalable**: Easy to add more services

## 🤝 Contributing

1. Create feature branch: `git checkout -b feature/my-feature`
2. Make changes
3. Format & test: `make check`
4. Commit: `git commit -m "feat: add feature"`
5. Push & create PR

## 📄 License

MIT License

---

**Built with ❤️ using Pants Build System**
