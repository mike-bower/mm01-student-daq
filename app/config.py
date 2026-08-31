"""
Settings for the MM01 StudentDAQ kit.

Values come from environment variables or a `.env` file in the project root.
See `.env.example` — every setting has a working default, so no .env is needed
when a real MM01 is plugged in.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Scan for connected MM01 devices at startup.
    mm01_auto_scan: bool = True

    # How often a reading is published to the browser, in milliseconds. The
    # device converts at a fixed 80 samples/second regardless of this value.
    mm01_poll_interval_ms: int = 200

    # Use simulated devices instead of real USB hardware. Lets Labs 1-4 run
    # with no MM01 attached.
    mm01_sim_enabled: bool = False
    mm01_sim_count: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
