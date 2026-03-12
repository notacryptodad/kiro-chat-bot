FROM python:3.11-slim

# System deps for subprocess/process management used by acp_client.py
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl procps git && \
    rm -rf /var/lib/apt/lists/*

# kiro-cli is expected via volume mount from host's ~/.kiro
# The mount provides both the binary and auth credentials
# Mount: -v ~/.kiro:/root/.kiro:ro
ENV PATH="/root/.kiro/bin:${PATH}"

WORKDIR /app

# Install Python deps via pip (no need for uv inside the container)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY acp_client.py kiro_bridge.py telegram_bot.py heartbeat.py heartbeat.md SOUL.md ./

# Default environment variables (override at runtime)
ENV TELEGRAM_BOT_TOKEN="" \
    ALLOWED_USER_IDS="" \
    KIRO_CLI_PATH="/root/.kiro/bin/kiro-cli" \
    KIRO_WORKING_DIR="/workspace" \
    HEARTBEAT_INTERVAL="900"

# Kiro working directory
RUN mkdir -p /workspace

ENTRYPOINT ["python", "telegram_bot.py"]
