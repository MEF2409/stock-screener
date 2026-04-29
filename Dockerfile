FROM python:3.12-slim

WORKDIR /app

# System deps for some yfinance/pandas internals (openssl for requests, build-essential for any wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first to maximize cache reuse.
# Prefer IPv4 over IPv6 when resolving PyPI: Fly's and Depot's builders have an
# unstable IPv6 path to Fastly's PyPI edge that RSTs mid-request, and pip never
# falls back from a broken v6 socket to v4. This forces getaddrinfo to return
# IPv4-mapped addresses first.
RUN echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf

ENV PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=15 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PREFER_BINARY=1 \
    UV_HTTP_TIMEOUT=300

# Use uv (Astral's Rust-based pip replacement) instead of pip — uv has more
# resilient retry/keepalive than urllib3, which matters because Fly/Depot's
# remote builder has been getting RST mid-handshake on PyPI requests.
# Builds now run on GitHub Actions runners (see .github/workflows/deploy.yml),
# so fetching the installer over the network here is reliable.
ADD https://astral.sh/uv/0.5.11/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && mv /root/.local/bin/uv /usr/local/bin/uv && rm /uv-installer.sh

COPY pyproject.toml requirements.txt ./

# Single resolution pass, system Python, no cache layer fluff.
RUN uv pip install --system --no-cache -r requirements.txt

# Copy source
COPY stock_screener/ ./stock_screener/
COPY scripts/ ./scripts/
COPY .streamlit/ ./.streamlit/

# Persistent SQLite + results live in /data (Fly.io volume mount target)
RUN mkdir -p /data/db /data/results && \
    ln -sf /data/db /app/db && \
    ln -sf /data/results /app/results

# Editable install so `from stock_screener.X import Y` resolves
RUN uv pip install --system --no-cache -e .

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
