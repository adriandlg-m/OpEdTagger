from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.infrastructure.config import load_config


def configure_logging() -> None:
    cfg = load_config()
    log_path = Path(cfg["log_path"]).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(handler, "_opedtagger", False) for handler in root.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=3_000_000, backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._opedtagger = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)
