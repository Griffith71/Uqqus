FROM ubuntu:24.04

COPY supervisord.conf /etc/supervisord.conf

RUN apt update \
    && apt install -y python3.12 python3-pip python3-venv supervisor

RUN mkdir -p /opt/ruqqus/service

COPY requirements.txt /opt/ruqqus/service/requirements.txt

RUN python3 -m venv /opt/ruqqus/service/venv \
    && /opt/ruqqus/service/venv/bin/pip install -r /opt/ruqqus/service/requirements.txt

EXPOSE 80/tcp

CMD [ "/usr/bin/supervisord", "-c", "/etc/supervisord.conf" ]
