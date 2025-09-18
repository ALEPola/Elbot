FROM python:3.12-slim
WORKDIR /app
COPY . /app

# Install ffmpeg for music playback then Python dependencies
RUN apt-get update \
    && apt-get install -y ffmpeg \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "-m", "elbot.main"]
