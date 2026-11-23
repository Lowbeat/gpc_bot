FROM mcr.microsoft.com/playwright/python:v1.65.2-jammy-arm64

WORKDIR /app

# No Edge needed on Linux ARM64 - we use Playwright's built-in Chromium via CDP
# (Edge is only used on Windows)

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
