FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && useradd --uid 10001 --create-home speech && mkdir /models && chown speech /models
COPY server.py benchmark.py .
USER speech
ENV HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
