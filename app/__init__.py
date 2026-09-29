"""Веб-застосунок генерації розкладу."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

from .jobs import JobStore

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        DATA_DIR=Path(os.environ.get("DATA_DIR", BASE_DIR / "data")),
        SAMPLE_INPUT=Path(os.environ.get("SAMPLE_INPUT", BASE_DIR / "info" / "Розклад на 2026 р (інформація).xlsx")),
        DEFAULT_TIME_LIMIT=float(os.environ.get("SOLVER_TIME_LIMIT", 120)),
    )
    app.extensions["jobs"] = JobStore(app.config["DATA_DIR"], workers=int(os.environ.get("SOLVER_WORKERS", 0)))

    from .routes import bp

    app.register_blueprint(bp)
    return app
