FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080

# Concurrency, not CPU, is the whole job here — every request is spent waiting
# on me-west1. One worker with a generous async loop is the right shape.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
