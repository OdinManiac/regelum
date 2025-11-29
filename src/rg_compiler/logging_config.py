from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from loguru import logger

_CONFIGURED = False


def _level_filter(level: str) -> Callable[[dict], bool]:
    def _filter(record: dict) -> bool:
        return record["level"].name == level

    return _filter


def configure_logging(
    service: str = "rg-compiler",
    version: str = os.getenv("RG_VERSION", "0.1.0"),
    environment: str = os.getenv("RG_ENV", "dev"),
) -> None:
    """
    Configure Loguru sinks:
      • logs/YYYY-MM-DD/debug.json
      • logs/YYYY-MM-DD/info.json
      • logs/YYYY-MM-DD/error.json
    All files are JSON lines with mandatory metadata fields.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    if os.getenv("RG_DISABLE_FILE_LOGS") == "1":
        logger.remove()
        logger.add(sys.stderr, level="INFO", colorize=sys.stderr.isatty(), enqueue=False)
        logger.configure(
            extra={
                "service": service,
                "version": version,
                "env": environment,
                "request_id": None,
                "user_id": None,
            }
        )
        _CONFIGURED = True
        return

    logger.remove()

    day_dir = Path("logs") / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    common_kwargs = {
        "serialize": True,
        "rotation": "10 MB",
        "retention": "30 days",
        "enqueue": True,
    }

    logger.add(
        day_dir / "debug.json",
        level="DEBUG",
        filter=_level_filter("DEBUG"),
        **common_kwargs,
    )
    logger.add(
        day_dir / "info.json",
        level="INFO",
        filter=_level_filter("INFO"),
        **common_kwargs,
    )
    logger.add(
        day_dir / "error.json",
        level="ERROR",
        filter=_level_filter("ERROR"),
        **common_kwargs,
    )

    if sys.stderr.isatty():
        logger.add(sys.stderr, level="INFO", colorize=True, enqueue=True)

    logger.configure(
        extra={
            "service": service,
            "version": version,
            "env": environment,
            "request_id": None,
            "user_id": None,
        }
    )

    _CONFIGURED = True
