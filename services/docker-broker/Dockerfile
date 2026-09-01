FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir docker==7.1.0

# Runs as root: it needs the Docker socket. Keep the API surface fixed.
COPY . /app

EXPOSE 9100
CMD ["python", "-c", "import time; print('docker-broker: not implemented yet', flush=True); time.sleep(86400)"]
