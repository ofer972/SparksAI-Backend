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

# Run the application with Railway's PORT environment variable (5 workers for production)
CMD uvicorn main:app --host 0.0.0.0 --port $PORT --workers 5
