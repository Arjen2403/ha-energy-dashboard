#!/bin/sh
# Set DB-pad expliciet hier (Dockerfile ENV wordt door s6-overlay weggestript)
export HA_DB_PATH=/config/home-assistant_v2.db
cd /app
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000