# Minimal single-stage image for the ShopPilot HTTP API.
# Build:  docker build -t shoppilot .
# Run:    docker run --rm -p 8000:8000 --env-file .env shoppilot
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Bundled demo data (small CSV catalog + knowledge base).
COPY data ./data

EXPOSE 8000
ENTRYPOINT ["shoppilot"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
