FROM python:3.12-slim

WORKDIR /app

# System deps for some yfinance/pandas internals (openssl for requests, build-essential for any wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first to maximize cache reuse
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source
COPY stock_screener/ ./stock_screener/
COPY scripts/ ./scripts/
COPY .streamlit/ ./.streamlit/

# Persistent SQLite + results live in /data (Fly.io volume mount target)
RUN mkdir -p /data/db /data/results && \
    ln -sf /data/db /app/db && \
    ln -sf /data/results /app/results

# Editable install so `from stock_screener.X import Y` resolves
RUN pip install --no-cache-dir -e .

# Entrypoint writes auth_config.yaml from $AUTH_CONFIG_YAML env var, then runs streamlit
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
