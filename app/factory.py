"""
Flask アプリケーションの作成・設定
"""

import logging
from pathlib import Path

from flask import Flask, send_from_directory
from flask_cors import CORS

from app.config import Config
from app.routes.api import api

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def create_app(config: Config | None = None) -> Flask:
    """Flask アプリを生成して返す"""
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
    CORS(app)

    if config is None:
        config = Config()
    app.config["APP_CONFIG"] = config

    # Blueprint 登録
    app.register_blueprint(api)

    # ルート: review.html を返す
    @app.get("/")
    def index():
        return send_from_directory(str(TEMPLATE_DIR), "review.html")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return app
