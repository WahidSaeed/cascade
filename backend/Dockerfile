# Build from the repository root:  docker build -f backend/Dockerfile -t cascade .
FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# Served as static files by FastAPI in single-container mode. In the AWS reference
# architecture the frontend is hosted separately on S3 + CloudFront.
COPY frontend ./frontend

# Defaults for AWS; override per environment. For a local container run, pass
# -e CASCADE_STORE_BACKEND=sqlite -e CASCADE_HEAVY_BACKEND=mock -e CASCADE_HANDLER_RUNTIME=subprocess
ENV CASCADE_STORE_BACKEND=dynamodb \
    CASCADE_HEAVY_BACKEND=bedrock \
    CASCADE_EMBED_BACKEND=bedrock \
    CASCADE_HANDLER_RUNTIME=lambda

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
