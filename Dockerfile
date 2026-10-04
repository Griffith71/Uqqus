# Builds the chat page's browser bundle (matrix-js-sdk + E2EE). This is the
# only place Node.js is needed - the runtime image below stays Node-free.
FROM node:20-slim AS chat-bundle

WORKDIR /opt/build/ruqqus/assets/chat_src
COPY ruqqus/assets/chat_src/package.json ./
RUN npm install
COPY ruqqus/assets/chat_src/src ./src
RUN npm run build

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

COPY --from=chat-bundle /opt/build/ruqqus/assets/js/chat_bundle.js ./ruqqus/assets/js/chat_bundle.js
COPY --from=chat-bundle /opt/build/ruqqus/assets/js/matrix_sdk_crypto_wasm_bg.wasm ./ruqqus/assets/js/matrix_sdk_crypto_wasm_bg.wasm

# Ensure FontAwesome and other docs assets are available under ./assets at runtime
# so Flask's send_from_directory('./assets', path) can find them. Copy docs/assets
# into the application assets directory at build time.
RUN mkdir -p assets && \
    if [ -d docs/assets/fontawesome ]; then \
        cp -r docs/assets/fontawesome assets/; \
    fi

EXPOSE 80/tcp

CMD [ "/usr/bin/supervisord", "-c", "/etc/supervisord.conf" ]
