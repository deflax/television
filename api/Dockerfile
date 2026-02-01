FROM python:3

RUN apt-get update && apt-get install -y --no-install-recommends \
ffmpeg \
&& \
apt-get clean && \
rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY ./scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY ./scripts/run.sh /run.sh
RUN chmod +x /run.sh

COPY ./app /app
WORKDIR /app/

ENV PYTHONPATH=/app

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/run.sh"]

