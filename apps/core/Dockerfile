FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy project files for pip install
COPY pyproject.toml ./
COPY apps/core/ /app/apps/core/
COPY shared/ /app/shared/

# Install the core API runtime, including the ASGI/web stack.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[core,web]"

ENV PYTHONPATH=/app
EXPOSE 8001
CMD ["uvicorn", "apps.core.src.main:app", "--host", "0.0.0.0", "--port", "8001"]
