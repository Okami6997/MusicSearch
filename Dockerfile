FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r songsfetch && useradd -r -g songsfetch -m songsfetch

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /music /data && \
    chown songsfetch:songsfetch /music /data

USER songsfetch

ENV PYTHONUNBUFFERED=1
ENV SONGSFETCH_OUTPUT_DIR=/music
ENV SONGSFETCH_DATA_DIR=/data

EXPOSE 3000

CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "-b", "0.0.0.0:3000", "app:app"]
