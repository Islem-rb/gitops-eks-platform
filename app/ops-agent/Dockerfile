# Build from the repository root, because the image includes docs/runbooks:
#   docker build -f app/ops-agent/Dockerfile -t platform/ops-agent:local .
FROM python:3.12-slim AS build
WORKDIR /w
COPY app/ops-agent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN useradd -r -u 10001 app
COPY --from=build /install /usr/local
WORKDIR /app
COPY app/ops-agent/main.py .
COPY app/ops-agent/agent ./agent
COPY docs/runbooks ./runbooks
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
