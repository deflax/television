FROM debian:stable-slim

ENV DEBIAN_FRONTEND noninteractive

RUN apt-get -qq -y update; \
  apt-get -qq -y full-upgrade; \ 
  apt-get -qq -y install icecast2 python3-setuptools; \
  apt-get -y autoclean; \
  apt-get clean;

RUN useradd radio ;\
  chown -R radio:radio /var/log/icecast2

ADD ./start.sh /start.sh

CMD ["/start.sh"]
