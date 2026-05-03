ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.11-alpine3.19
FROM $BUILD_FROM

# git nodig om de dashboard repo te clonen tijdens build
RUN apk add --no-cache git

# Clone de dashboard code
ARG REPO_URL=https://github.com/Arjen2403/ha-energy-dashboard.git
ARG REPO_REF=main
ARG CACHEBUST=2
RUN git clone --depth 1 --branch $REPO_REF $REPO_URL /app

# Install dependencies direct, niet via project-install.
# Dit omzeilt setuptools package-discovery problemen.
RUN pip3 install --break-system-packages \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.27" \
    "pandas>=2.2" \
    "cachetools>=5.3" \
    "python-dotenv>=1.0" \
    "pydantic>=2.6" \
    "tzdata>=2024.1"

# DB-pad binnen container
ENV HA_DB_PATH=/config/home-assistant_v2.db

# Run script
COPY run.sh /
RUN chmod +x /run.sh

CMD ["/run.sh"]