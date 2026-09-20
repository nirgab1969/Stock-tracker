import logging
import os
from pathlib import Path

from flask import Flask, send_from_directory

from . import config, db, scheduler
from .push import service as push_service
from .routes.api import api_bp

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


def create_app(start_scheduler: bool = True) -> Flask:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

    db.init_db()
    push_service.init()

    app.register_blueprint(api_bp)

    @app.get("/")
    def index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.get("/service-worker.js")
    def service_worker():
        # Must be served from the root (not /static/...) so its scope covers the whole app.
        resp = send_from_directory(str(FRONTEND_DIR), "service-worker.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/manifest.json")
    def manifest():
        return send_from_directory(str(FRONTEND_DIR), "manifest.json")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    if start_scheduler and os.environ.get("DISABLE_SCHEDULER") != "1":
        scheduler.start_background_scheduler()

    return app
