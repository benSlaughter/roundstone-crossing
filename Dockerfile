# Stage 1: Install dependencies and run tests
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python -m pytest tests/ -q

# Stage 2: Production image
FROM python:3.12-slim AS runner
WORKDIR /app

# Install only runtime dependencies (no pytest/freezegun)
COPY requirements.txt .
RUN pip install --no-cache-dir \
    "stomp.py>=8.1.0" \
    "fastapi>=0.115.0" \
    "uvicorn>=0.34.0" \
    "pydantic>=2.0.0" \
    "pyyaml>=6.0" \
    "requests>=2.31.0"

# Create non-root user
RUN groupadd --system --gid 1001 crossing && \
    useradd --system --uid 1001 --gid crossing crossing

# Copy application code
COPY src/ ./src/
COPY static/ ./static/
COPY config.yaml .

# Create data directories
RUN mkdir -p /app/data /app/logs && chown -R crossing:crossing /app/data /app/logs

USER crossing

ENV CROSSING_DB_PATH=/app/data/crossing.db

EXPOSE 8590

CMD ["python", "-m", "src.main", "--api"]
