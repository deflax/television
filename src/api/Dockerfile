FROM python:3-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
ffmpeg \
&& \
apt-get clean && \
rm -rf /var/lib/apt/lists/*

WORKDIR /python-docker

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY requirements-pydantic-pin.txt requirements-pydantic-pin.txt
RUN pip3 install -r requirements-pydantic-pin.txt
RUN pip3 install https://github.com/datarhei/core-client-python/archive/refs/tags/1.1.0.tar.gz

COPY . .

EXPOSE 8080

CMD [ "waitress-serve", "--port=8080", "--trusted-proxy", "*", "--trusted-proxy-headers", "forwarded", "--log-untrusted-proxy-headers", "--threads", "16", "--call", "api:create_app" ]