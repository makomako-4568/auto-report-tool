"""
設定管理モジュール
.env と configs/settings.yaml を統合して返す
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# プロジェクトルート
ROOT_DIR = Path(__file__).parent.parent
ENV_FILE = ROOT_DIR / ".env"
SETTINGS_FILE = ROOT_DIR / "configs" / "settings.yaml"
TOKEN_CACHE_FILE = ROOT_DIR / ".msal_token_cache.json"

load_dotenv(ENV_FILE)


def load_settings() -> dict:
    """settings.yaml を読み込み、環境変数で上書きして返す"""
    settings = {}
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}

    # 環境変数による上書き
    if os.getenv("REDMINE_PROJECTS"):
        settings["redmine_projects"] = [
            p.strip() for p in os.getenv("REDMINE_PROJECTS", "").split(",") if p.strip()
        ]
    if os.getenv("ONENOTE_NOTEBOOKS"):
        settings["onenote_notebooks"] = [
            n.strip() for n in os.getenv("ONENOTE_NOTEBOOKS", "").split(",") if n.strip()
        ]
    if os.getenv("ONENOTE_SECTIONS"):
        settings["onenote_sections"] = [
            s.strip() for s in os.getenv("ONENOTE_SECTIONS", "").split(",") if s.strip()
        ]
    if os.getenv("AI_MODEL"):
        settings["ai_model"] = os.getenv("AI_MODEL")

    return settings


class Config:
    """環境変数 + settings.yaml を統合した設定オブジェクト"""

    def __init__(self):
        self._settings = load_settings()

        # Microsoft Graph
        self.ms_tenant_id: str = os.getenv("MS_TENANT_ID", "")
        self.ms_client_id: str = os.getenv("MS_CLIENT_ID", "")

        # Redmine
        self.redmine_url: str = os.getenv("REDMINE_URL", "").rstrip("/")
        self.redmine_api_key: str = os.getenv("REDMINE_API_KEY", "")
        self.redmine_projects: list[str] = self._settings.get("redmine_projects", [])

        # OneNote
        self.onenote_notebooks: list[str] = self._settings.get("onenote_notebooks", [])
        self.onenote_sections: list[str] = self._settings.get("onenote_sections", ["TODO", "日報"])

        # AI
        self.github_token: str = os.getenv("GITHUB_TOKEN", "")
        self.ai_model: str = self._settings.get("ai_model", "gpt-4o-mini")

        # 報告先
        self.boss_email: str = os.getenv("BOSS_EMAIL", "")
        self.boss_email_subject: str = os.getenv("BOSS_EMAIL_SUBJECT", "課週間ミーティング（書面）")

        # Flask
        self.flask_port: int = int(os.getenv("FLASK_PORT", str(self._settings.get("flask_port", 5000))))
        self.flask_debug: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"

        # 実行モード
        self.mode: str = self._settings.get("mode", "interactive")

    def validate(self) -> list[str]:
        """設定値の検証。不足・不正な項目のリストを返す"""
        errors = []
        if not self.ms_tenant_id:
            errors.append("MS_TENANT_ID が未設定です")
        if not self.ms_client_id:
            errors.append("MS_CLIENT_ID が未設定です")
        if not self.redmine_url:
            errors.append("REDMINE_URL が未設定です")
        if not self.redmine_api_key:
            errors.append("REDMINE_API_KEY が未設定です")
        if not self.github_token:
            errors.append("GITHUB_TOKEN が未設定です")
        if not self.boss_email:
            errors.append("BOSS_EMAIL が未設定です")
        return errors
