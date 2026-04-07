FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cache bust: 2026-04-07-v2
EXPOSE 8000

CMD sh -c "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"
