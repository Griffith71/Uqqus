FROM python:3.11-slim

COPY supervisord.conf /etc/supervisord.conf

RUN apt-get update \
    && apt-get install -y --no-install-recommends supervisor build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/ruqqus/service
WORKDIR /opt/ruqqus/service

COPY requirements.txt .

# Use a virtual environment to isolate installs (keeps same layout as before)
RUN python -m venv /opt/ruqqus/service/venv \
    && /opt/ruqqus/service/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/ruqqus/service/venv/bin/pip install -r requirements.txt

COPY . .

# Ensure FontAwesome and other docs assets are available under ./assets at runtime
# so Flask's send_from_directory('./assets', path) can find them. Copy docs/assets
# into the application assets directory at build time.
RUN mkdir -p assets && \
    if [ -d docs/assets/fontawesome ]; then \
        cp -r docs/assets/fontawesome assets/; \
    fi

EXPOSE 80/tcp

CMD [ "/usr/bin/supervisord", "-c", "/etc/supervisord.conf" ]
