"""FastAPI entry point voor het HA energy dashboard."""
from fastapi import FastAPI

from .routers import flows, meta

app = FastAPI(
    title="HA Energy Dashboard",
    description="Browser-native energie-dashboard voor full-electric woning",
    version="0.1.0",
)

app.include_router(meta.router, prefix="/api")
app.include_router(flows.router, prefix="/api")