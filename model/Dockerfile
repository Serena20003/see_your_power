# Dockerfile for Cloud Run jobs (pipeline + inference)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV USE_FIXTURES=false
ENV PYTHONUNBUFFERED=1

# Default: run the inference batch job
CMD ["python", "-m", "inference.batch"]
