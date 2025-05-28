# Dockerfile for ELBOT
# Use ARM64v8 Python image for Raspberry Pi 4
FROM arm64v8/python:3.11-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]
# Tip: Rebuild this image regularly to get the latest security patches.
