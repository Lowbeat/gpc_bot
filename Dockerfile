FROM mcr.microsoft.com/playwright/python:v1.65.2-jammy-arm64

WORKDIR /app

# Install Microsoft Edge for Ozon CDP (ARM64) - try direct .deb download
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    ca-certificates \
    && wget -q https://go.microsoft.com/fwlink?linkid=2149051 -O /tmp/edge.deb \
    && apt-get install -y --no-install-recommends /tmp/edge.deb \
    && rm -f /tmp/edge.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
