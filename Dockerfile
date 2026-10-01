# Telegram Buyer Collector - Dockerfile
# Base Image: Python 3.13 Slim Debian
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for Telethon SSL and network
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt aiohttp requests

# Explicitly copy ONLY collector modules
# Excludes database.py, db_server.py, and all database files to ensure absolute isolation
COPY config.py .
COPY db_client.py .
COPY llm_analyzer.py .
COPY lock_manager.py .
COPY storage_handler.py .
COPY autonomous_collector.py .
COPY test_*.py .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV DATABASE_API_URL=http://telegram_database_api:8000

# Default entrypoint runs the persistent worker via REST API
ENTRYPOINT ["python3", "autonomous_collector.py"]
CMD ["worker", "--limit", "15"]
