FROM python:3.11-slim

# Install system dependencies for python-olm and other build tools
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libolm-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Create non-root user
RUN useradd -m -u 1000 beeperclaw && chown -R beeperclaw:beeperclaw /app
USER beeperclaw

# Copy configuration
COPY config.example.yaml config.yaml

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV MATRIX_HOMESERVER=${MATRIX_HOMESERVER:-https://matrix.beeper.com}
ENV MATRIX_USERNAME=${MATRIX_USERNAME}
ENV MATRIX_PASSWORD=${MATRIX_PASSWORD}
ENV OPENCODE_SERVER_URL=${OPENCODE_SERVER_URL:-http://host.docker.internal:4096}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Entrypoint
ENTRYPOINT ["beeperclaw"]
CMD ["run"]