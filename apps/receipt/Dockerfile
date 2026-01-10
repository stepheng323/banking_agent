FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy project files for pip install
COPY pyproject.toml ./
COPY apps/receipt /app/apps/receipt
COPY shared /app/shared

# Install base + receipt dependencies only
RUN pip install --no-cache-dir ".[receipt]"

# Install playwright browser
RUN playwright install chromium
RUN playwright install-deps chromium

ENV PYTHONPATH=/app
CMD ["uvicorn", "apps.receipt.main:app", "--host", "0.0.0.0", "--port", "8002"]
