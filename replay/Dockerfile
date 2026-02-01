FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY ./scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY ./scripts/run.sh /run.sh
RUN chmod +x /run.sh

COPY ./hypercorn.toml /app/hypercorn.toml
COPY ./app /app
WORKDIR /app/

ENV PYTHONPATH=/app

EXPOSE 8090

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/run.sh"]
