ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.11-alpine3.19
FROM $BUILD_FROM

# HA aarch64-base-python image is gebaseerd op Alpine Linux + Python.
# git nodig om de dashboard repo te clonen tijdens build.
RUN apk add --no-cache git

# Clone de dashboard code op een specifieke branch/tag.
# Gebruik build args zodat we later kunnen pinnen op een commit-SHA.
ARG REPO_URL=https://github.com/Arjen2403/ha-energy-dashboard.git
ARG REPO_REF=main
RUN git clone --depth 1 --branch $REPO_REF $REPO_URL /app

# Installeer Python dependencies via pyproject.toml
WORKDIR /app
RUN pip3 install --break-system-packages -e .

# DB-pad binnen container — /config wordt gemount via add-on config map: config:ro
ENV HA_DB_PATH=/config/home-assistant_v2.db

# Run script uit add-on dir kopieren en uitvoerbaar maken
COPY run.sh /
RUN chmod +x /run.sh

CMD ["/run.sh"]