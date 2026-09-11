FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY cli/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy CLI source so "kiddo_cli" is a package under /app/src
COPY cli/src /app/src
ENV PYTHONPATH=/app/src

ENTRYPOINT ["python", "-m", "kiddo_cli.main"]
