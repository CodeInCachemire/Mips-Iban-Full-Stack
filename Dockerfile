FROM eclipse-temurin:17-jre

WORKDIR /app

# Install Python, venv, curl (curl needed for HEALTHCHECK)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Backend deps
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# App files
COPY mars.jar /app/mars.jar
COPY src /app/src
COPY backend /app/backend
COPY frontend /app/frontend

# Non-root user (disabled for Docker on Windows volume compatibility)
# RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
# USER appuser

ENV PORT=8000

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/ || exit 1

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]