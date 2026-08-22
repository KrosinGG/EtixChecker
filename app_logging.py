from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def init_logger(log_dir: Path) -> logging.Logger:
    logger = logging.getLogger("etix_checker")
    if logger.handlers:
        return logger

    log_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    app_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.DEBUG)
    app_handler.setFormatter(fmt)

    err_handler = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(fmt)

    logger.addHandler(app_handler)
    logger.addHandler(err_handler)
    logger.propagate = False
    return logger
