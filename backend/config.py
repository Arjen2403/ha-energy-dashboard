"""Centrale configuratie. Leest uit .env via python-dotenv."""
import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    ha_db_path: str
    cache_ttl_hourly: int = 300
    cache_ttl_daily: int = 3600
    cache_ttl_monthly: int = 86400
    dashboard_port: int = 8000


settings = Settings(
    ha_db_path=os.environ["HA_DB_PATH"],
    cache_ttl_hourly=int(os.getenv("CACHE_TTL_HOURLY", 300)),
    cache_ttl_daily=int(os.getenv("CACHE_TTL_DAILY", 3600)),
    cache_ttl_monthly=int(os.getenv("CACHE_TTL_MONTHLY", 86400)),
    dashboard_port=int(os.getenv("DASHBOARD_PORT", 8000)),
)