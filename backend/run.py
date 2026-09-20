#!/usr/bin/env python3
"""
Entry point for local development / simple production runs.
For real production deployment, run with gunicorn instead - see README.md.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from app import config, create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, use_reloader=False)
