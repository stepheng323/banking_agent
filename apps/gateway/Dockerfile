FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy workspace files
COPY pyproject.toml poetry.lock ./
COPY shared/ /shared/
COPY gateway/ /app/

# Install dependencies
RUN poetry config virtualenvs.create false
RUN poetry install --only=main

# Expose port
EXPOSE 8000

# Run the application
CMD ["poetry", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
