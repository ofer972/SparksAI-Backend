FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway handles port binding automatically)
EXPOSE 8000

# Run the application with smart worker detection
# WORKERS env var controls worker count (default: 1 for K8s, can override)
# For Railway/production: set WORKERS=5 environment variable
# For Kubernetes: use pod replicas instead, keep WORKERS=1 (default)
CMD sh -c 'WORKERS=${WORKERS:-${KUBERNETES_SERVICE_HOST:+1}}; WORKERS=${WORKERS:-5}; echo "Starting with $WORKERS workers"; uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers $WORKERS --no-access-log'
