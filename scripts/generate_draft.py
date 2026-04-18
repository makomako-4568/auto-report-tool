"""
GitHub Actions 用：週次報告ドラフトを生成して Outlook に保存する
（対話なし・Device Code Flow 不要の Client Credentials Flow を使用）

GitHub Secrets に以下を設定してください：
  MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET
  REDMINE_URL, REDMINE_API_KEY
  GITHUB_TOKEN, BOSS_EMAIL, BOSS_EMAIL_SUBJECT
"""

import os
import sys
import logging

import msal
import requests

# プロジェクトルートを Python パスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config
from app.collect.redmine import fetch_tickets, get_week_range
from app.generate.report import generate_report
from app.send.email import save_draft

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# GitHub Actions では Client Credentials Flow を使用
# (対話型認証が使えないため)
SCOPES_APP = ["https://graph.microsoft.com/.default"]


def get_access_token_app(config: Config) -> str:
    """
    Client Credentials Flow でアクセストークンを取得する。
    GitHub Actions など非対話環境で使用。
    MS_CLIENT_SECRET が必要。
    """
    client_secret = os.getenv("MS_CLIENT_SECRET", "")
    if not client_secret:
        raise RuntimeError("MS_CLIENT_SECRET が未設定です（GitHub Secrets を確認してください）")

    app = msal.ConfidentialClientApplication(
        client_id=config.ms_client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{config.ms_tenant_id}",
    )
    result = app.acquire_token_for_client(scopes=SCOPES_APP)
    if "access_token" not in result:
        raise RuntimeError(f"認証失敗: {result.get('error_description', result.get('error'))}")
    return result["access_token"]


def fetch_onenote_simple(access_token: str, config: Config) -> list[dict]:
    """OneNote からシンプルにテキストを取得する（GitHub Actions 向け簡易版）"""
    from app.collect.onenote import fetch_onenote_texts
    return fetch_onenote_texts(access_token, config)


def main():
    config = Config()
    week_offset = int(os.getenv("WEEK_OFFSET", "0"))

    # 設定チェック
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error("設定エラー: %s", e)
        sys.exit(1)

    monday, sunday = get_week_range(week_offset)
    week_label = f"{monday.strftime('%Y/%m/%d')}〜{sunday.strftime('%m/%d')}"
    logger.info("対象週: %s", week_label)

    # 認証
    logger.info("Microsoft 認証中（Client Credentials Flow）...")
    access_token = get_access_token_app(config)

    # データ収集
    logger.info("Redmine チケット収集中...")
    tickets = fetch_tickets(config, week_offset)
    logger.info("Redmine: %d 件", len(tickets))

    logger.info("OneNote 収集中...")
    onenote_pages = fetch_onenote_simple(access_token, config)
    logger.info("OneNote: %d ページ", len(onenote_pages))

    # レポート生成
    logger.info("AI でレポート生成中...")
    report = generate_report(config, tickets, onenote_pages, week_label)

    # Outlook に下書き保存
    logger.info("Outlook に下書きを保存中...")
    draft_id = save_draft(access_token, config, report)
    logger.info("下書き保存完了: %s", draft_id)

    print("\n" + "=" * 60)
    print(f"週次報告ドラフトを Outlook の下書きに保存しました")
    print(f"対象週: {week_label}")
    print(f"宛先: {config.boss_email}")
    print("=" * 60)
    print("\n--- 生成されたレポート ---")
    print(report)


if __name__ == "__main__":
    main()
