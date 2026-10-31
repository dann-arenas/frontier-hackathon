FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=9000 \
    HOST=0.0.0.0 \
    STATE_DIR=/app/state \
    OUTPUT_DIR=/app/output \
    RUN_MODE=eval \
    PSEUDONYM_SECRET=9f4c8e2a71d053b6e91c4a82f70d3e5b18a96c24d7e03f518b62c49a3d7e10f8 \
    SOURCES_URL=https://reto-pii-fuentes-production.up.railway.app/v1

# Install system build dependencies and Node.js 20+ (with Debian nodejs fallback) for `node validar.mjs`
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    ca-certificates \
    curl \
    gnupg \
    && (mkdir -p /etc/apt/keyrings \
        && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
        && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
        && apt-get update && apt-get install -y --no-install-recommends nodejs \
        || apt-get install -y --no-install-recommends nodejs) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY . .
RUN chmod +x run.sh && mkdir -p /app/state /app/output /app/logs

VOLUME ["/app/state", "/app/output"]

EXPOSE 9000

CMD ["./run.sh"]
