FROM python:3.13-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
ffmpeg \
&& \
apt-get clean && \
rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY requirements-pydantic-pin.txt requirements-pydantic-pin.txt
RUN pip3 install -r requirements-pydantic-pin.txt
RUN pip3 install https://github.com/datarhei/core-client-python/archive/refs/tags/1.1.0.tar.gz

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

