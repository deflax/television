FROM python:3-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
ffmpeg \
&& \
apt-get clean && \
rm -rf /var/lib/apt/lists/*

WORKDIR /python-docker

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

CMD ["python", "./discordbot.py"]