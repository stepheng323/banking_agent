# Banking Agent - WhatsApp Banking Chatbot

A Python monorepo for a WhatsApp-based banking agent with LLM processing and automatic receipt generation.

## 🏗️ Architecture

**Python Monorepo** with standard Python tooling:

- **Gateway Service** (`apps/gateway/`) - WhatsApp webhook + LangGraph LLM agent
- **Core Service** (`apps/core/`) - Banking operations + Receipt generation
- **Shared Library** (`shared/`) - Common models, utilities, and clients

## 🚀 Quick Start

### 1. Setup

```bash
# Run setup script (creates venv, installs deps)
bash scripts/setup.sh

# Or manually:
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Set PYTHONPATH for monorepo imports
export PYTHONPATH=$(pwd):$PYTHONPATH

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
make run-gateway      # Gateway on port 8000
make run-core         # Core on port 8001
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
make format           # Format code with ruff
make lint             # Lint code with ruff
make test             # Run tests with pytest
make check-all        # Run all checks (lint + type-check + format)
make clean            # Clean Python caches
```

### Running Services

```bash
# Start gateway service
make run-gateway      # uvicorn apps.gateway.main:app --reload

# Start core service
make run-core         # uvicorn apps.core.src.main:app --reload

# Or run directly
uvicorn apps.gateway.main:app --reload --port 8000
uvicorn apps.core.src.main:app --reload --port 8001
```

### Testing

```bash
make test             # Run all tests
make test-file FILE=tests/test_agent.py  # Run specific test
make test-coverage    # Run tests with coverage
```

## 📁 Project Structure

```
banking_agent/
├── apps/
│   ├── gateway/              # WhatsApp + LangGraph
│   │   ├── main.py
│   │   ├── api/             # FastAPI routes
│   │   ├── adapters/        # WhatsApp, sender
│   │   └── core/            # Config
│   └── core/                # Banking + Receipts
│       └── src/
│           └── main.py
├── shared/                  # Shared library
│   ├── models/
│   ├── config/
│   ├── clients/
│   └── utils/
├── requirements.txt         # Python dependencies
├── docker-compose.yml      # Docker setup
├── Makefile               # Development commands
└── scripts/               # Utility scripts
    └── setup.sh           # Setup script
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
# 1. Add to requirements.txt
echo "new-package==1.0.0" >> requirements.txt

# 2. Install new dependency
pip install new-package==1.0.0

# 3. (Optional) Update requirements.txt with exact versions
pip freeze > requirements.txt
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

### Deployment Options

The Dockerfiles use `requirements.txt` and work with any container platform:
- AWS ECS/Fargate
- Google Cloud Run
- Azure Container Instances
- Kubernetes
- Railway, Fly.io, Render, etc.

## 🧪 Testing

```bash
# All tests
make test
# or: pytest

# Specific test file
make test-file FILE=tests/test_agent.py
# or: pytest tests/test_agent.py

# With coverage
make test-coverage
# or: pytest --cov=apps --cov=shared --cov-report=term-missing
```

## 🔧 Troubleshooting

### Python Import Errors

If you get `ModuleNotFoundError` when importing from `shared` or `apps`:

```bash
# Set PYTHONPATH (add to your .env or shell profile)
export PYTHONPATH=$(pwd):$PYTHONPATH

# Or activate virtual environment with PYTHONPATH
source .venv/bin/activate
export PYTHONPATH=$(pwd):$PYTHONPATH
```

### Clean Python Caches

```bash
make clean
# Removes __pycache__, .pytest_cache, .mypy_cache, etc.
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
# Create/activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Point IDE to:
# .venv/bin/python  (or .venv\Scripts\python.exe on Windows)

# Set PYTHONPATH in IDE settings:
# PYTHONPATH=/path/to/banking_agent
```

## 📚 Tech Stack

- **Language**: Python 3.13
- **Web**: FastAPI + Uvicorn
- **LLM**: LangChain + LangGraph + OpenAI
- **Database**: PostgreSQL 16
- **Cache**: Redis 7
- **Cloud**: AWS (S3, SES, RDS)
- **Container**: Docker + Docker Compose
- **Tooling**: Ruff (linting), MyPy (type checking), Pytest (testing)

## 🎯 Key Features

- ✅ **Monorepo**: Single codebase, independent services
- ✅ **Type Safe**: Pydantic models throughout
- ✅ **Fast Development**: Instant startup, hot reload
- ✅ **Production Ready**: Docker images for easy deployment
- ✅ **LLM Agent**: LangGraph for conversational banking
- ✅ **Auto Receipts**: Automatic receipt generation
- ✅ **Scalable**: Easy to add more services
- ✅ **Standard Tooling**: Uses standard Python tools (pip, pytest, ruff)

## 🤝 Contributing

1. Create feature branch: `git checkout -b feature/my-feature`
2. Make changes
3. Format & test: `make check`
4. Commit: `git commit -m "feat: add feature"`
5. Push & create PR

## 📄 License

MIT License

---

**Built with ❤️ using Python and FastAPI**
