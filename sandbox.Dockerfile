FROM python:3.12-slim

# Prevent Python from writing pyc files to disk and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install runtime dependencies for the sandbox
RUN pip install --no-cache-dir \
    clickhouse-connect==0.7.16 \
    psycopg2-binary==2.9.9 \
    paramiko==3.4.0 \
    cryptography==42.0.5

WORKDIR /workspace
