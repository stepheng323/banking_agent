# Receipt Worker Dockerfile
# Uses Playwright for HTML to image rendering

FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for Playwright
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

# Copy requirements and install dependencies
COPY apps/receipt_worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and its browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY apps/receipt_worker /app/apps/receipt_worker
COPY shared /app/shared

# Set Python path
ENV PYTHONPATH=/app

# Run the worker
CMD ["python", "apps/receipt_worker/main.py"]
