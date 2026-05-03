#!/bin/sh
# Start FastAPI backend op poort 8000 (intern; extern wordt via config.yaml mapped)
cd /app
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000