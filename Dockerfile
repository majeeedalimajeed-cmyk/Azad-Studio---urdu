FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata \
    PYTHONPATH=/app:/app/vendor

RUN apt-get update \
 && apt-get install -y --no-install-recommends libraqm0 tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN useradd --create-home --shell /usr/sbin/nologin nrnews \
 && mkdir -p /app/data \
 && chown -R nrnews:nrnews /app/data

USER nrnews
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read()"

CMD ["python", "-m", "nr_news.cli", "serve"]
