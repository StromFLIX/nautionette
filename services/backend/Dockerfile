FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home --uid 10001 app
COPY --chown=app:app . /app
USER app

EXPOSE 8080
CMD ["python", "-c", "import time; print('backend: not implemented yet', flush=True); time.sleep(86400)"]
