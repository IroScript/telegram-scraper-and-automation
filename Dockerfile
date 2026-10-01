# Telegram Buyer Collector - Dockerfile
# Base Image: Python 3.13 Slim Debian
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for SQLite WAL and Telethon SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    sqlite3 \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure lock file and db are on persistent volume mounts
VOLUME ["/app/data"]

# Default entrypoint runs the persistent worker
ENTRYPOINT ["python3", "autonomous_collector.py"]
CMD ["worker", "--limit", "15"]
