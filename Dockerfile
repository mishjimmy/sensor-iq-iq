FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

RUN pip install --no-cache-dir "fastapi==0.115.6" "uvicorn[standard]==0.32.1" "httpx==0.28.1" \
    "pydantic-settings==2.6.1" "ruamel.yaml==0.18.6" "fpdf2==2.9.0" "tzdata"

COPY app ./app

# Run unprivileged. /data holds panels.yaml + history.db and must be a mounted volume
# (a directory, not a single-file mount: panels.yaml is saved via atomic rename).
RUN useradd --system --uid 10001 --home /srv sensoriq \
 && mkdir -p /data && chown sensoriq:sensoriq /data
USER sensoriq
VOLUME /data

ENV PANELS_FILE=/data/panels.yaml HISTORY_DB=/data/history.db HOST=0.0.0.0 PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"

CMD ["python", "-m", "app.main"]
