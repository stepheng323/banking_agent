FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy project files for pip install
COPY pyproject.toml ./
COPY apps/gateway/ /app/apps/gateway/
COPY shared/ /app/shared/

# Install base dependencies only (gateway has no extras)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "."

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "apps.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]