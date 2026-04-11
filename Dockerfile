FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --system cardioauth && useradd --system --gid cardioauth cardioauth

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
ARG CACHEBUST=1
COPY . .

# Create data directory with proper permissions
RUN mkdir -p /app/data && chown -R cardioauth:cardioauth /app

# Switch to non-root user
USER cardioauth

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

CMD sh -c "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"
