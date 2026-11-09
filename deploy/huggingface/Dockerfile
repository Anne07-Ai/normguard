FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY . /app
RUN python -m pip install --upgrade "pip==26.2.1" && \
    python -m pip install ".[service]" && \
    useradd --create-home --uid 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser
EXPOSE 7860

CMD ["uvicorn", "deploy.huggingface.app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
