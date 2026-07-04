FROM python:3.11-slim

LABEL description="K10 Compile Server - Compile and flash UniHiker K10 firmware via browser"
LABEL maintainer="K10 Community"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    zip \
    unzip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install PlatformIO
RUN pip install --no-cache-dir platformio && \
    mkdir -p /root/.platformio && \
    pio --version

# Install DFRobot UniHiker K10 platform
RUN pio platform install "https://github.com/DFRobot/platform-unihiker.git"

# Copy server code
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY static/ ./static/
COPY npm/ ./npm/
COPY stub_flasher/ ./stub_flasher/

# Port
EXPOSE 8900

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sk https://localhost:8900/api/health || curl -s http://localhost:8900/api/health || exit 1

# Default: run via main.py — it auto-detects SSL certs via env vars
# Mount cert.pem + key.pem and set K10_SSL_CERT / K10_SSL_KEY to enable HTTPS
ENV K10_STATIC_DIR=/app/static
ENV K10_NPM_DIR=/app/npm
ENV PYTHONUNBUFFERED=1

CMD ["python3", "main.py"]
