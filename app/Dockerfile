FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory to root to support the 'app' package structure
WORKDIR /

# Copy dependency files
COPY pyproject.toml /app/

# Install dependencies using uv
RUN uv pip install --system -r /app/pyproject.toml

# Copy application code
COPY . /app/

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
