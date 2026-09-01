FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir temporalio==1.9.0

RUN useradd --create-home --uid 10003 worker
COPY --chown=worker:worker . /app
USER worker

# Loads workflow definitions from the shared volume at /workflows.
CMD ["python", "-c", "import time; print('worker: not implemented yet', flush=True); time.sleep(86400)"]
