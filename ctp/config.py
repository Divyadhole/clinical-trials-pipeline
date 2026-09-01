"""Configuration, read once from the environment."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    database_url: str
    lookback_days: int
    requests_per_minute: int
    page_size: int
    max_retries: int

    @classmethod
    def from_env(cls) -> "Config":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env, or export it."
            )
        return cls(
            database_url=url,
            lookback_days=int(os.environ.get("CTP_LOOKBACK_DAYS", "2")),
            requests_per_minute=int(os.environ.get("CTP_REQUESTS_PER_MINUTE", "40")),
            page_size=int(os.environ.get("CTP_PAGE_SIZE", "1000")),
            max_retries=int(os.environ.get("CTP_MAX_RETRIES", "5")),
        )
