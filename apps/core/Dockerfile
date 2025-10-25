FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy workspace files
COPY pyproject.toml poetry.lock ./
COPY shared/ /shared/
COPY core/ /app/

# Install dependencies
RUN poetry config virtualenvs.create false
RUN poetry install --only=main

# Expose port
EXPOSE 8001

# Run the application
CMD ["poetry", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
