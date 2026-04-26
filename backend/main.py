"""FastAPI entry point voor het HA energy dashboard."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import costs, flows, meta, overview

app = FastAPI(
    title="HA Energy Dashboard",
    description="Browser-native energie-dashboard voor full-electric woning",
    version="0.1.0",
)

app.include_router(meta.router, prefix="/api")
app.include_router(flows.router, prefix="/api")
app.include_router(overview.router, prefix="/api")
app.include_router(costs.router, prefix="/api")

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")