FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy service-specific pyproject.toml
COPY apps/core/pyproject.toml /app/pyproject.toml

# Configure Poetry
RUN poetry config virtualenvs.create false

# Install dependencies
RUN poetry install --only=main

# Copy service code
COPY apps/core/ /app/
COPY shared/ /shared/

# Expose port
EXPOSE 8001

# Run the service
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]