FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies before copying source (layer cache)
RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0" \
    "python-multipart>=0.0.9" \
    "anthropic>=0.50" \
    "openai>=1.0" \
    "duckdb>=0.10" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.2" \
    "python-dotenv>=1.0" \
    "playwright>=1.44" \
    "pandas>=2.2" \
    "openpyxl>=3.1" \
    "rich>=13.0" \
    "beautifulsoup4>=4.12" \
    "httpx>=0.27" \
    "tenacity>=8.3" \
    "python-dateutil>=2.9" \
    "nltk>=3.8"

# Bake Chromium + all its system libs into the image
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium

# Copy source
COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "server.py"]
