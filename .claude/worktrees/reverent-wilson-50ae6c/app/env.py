from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@lru_cache(maxsize=1)
def load_app_env() -> None:
    load_dotenv(DEFAULT_ENV_FILE, override=False)
