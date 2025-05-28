# Dockerfile for ELBOT
# Use Python image for better security practices

# Build stage
FROM python:3.11-slim-bullseye AS builder

WORKDIR /build

# Copy only requirements first to leverage Docker cache
COPY requirements.txt .

# Install build dependencies and compile requirements
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Final stage
FROM gcr.io/distroless/python3-debian11

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/

# Copy application code
COPY --chown=elbot:elbot . /app/

# Install dependencies
COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/requirements.txt

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app:$PATH"

# Expose port for the bot
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import nextcord; exit(0)"

# Command to run
CMD ["python3", "main.py"]
# Tip: Rebuild this image regularly to get the latest security patches.
