FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    NBA_DB_HOST=127.0.0.1 \
    NBA_DB_PORT=3306 \
    NBA_DB_USER=nba_agent \
    NBA_DB_PASSWORD=nba_agent \
    NBA_DB_NAME=nba \
    NBA_AGENT_MODEL=deepseek-chat

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        default-mysql-server \
        default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agent /app/agent
COPY app /app/app
COPY data /app/data
COPY docker /app/docker
COPY sql /app/sql
COPY README.md /app/README.md

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /app/outputs/charts

EXPOSE 5000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
