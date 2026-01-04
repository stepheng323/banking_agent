FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY apps/core/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt
COPY apps/core/ /app/apps/core/
COPY shared/ /app/shared/
ENV PYTHONPATH=/app
EXPOSE 8001
CMD ["uvicorn", "apps.core.src.main:app", "--host", "0.0.0.0", "--port", "8001"]