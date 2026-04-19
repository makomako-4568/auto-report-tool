"""
GitHub Actions 用：毎朝タスク整理ダッシュボードを生成して Outlook に下書き保存する

GitHub Secrets に以下を設定してください：
  MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET
  REDMINE_URL, REDMINE_API_KEY
  GITHUB_TOKEN
  BOSS_EMAIL（送信先。自分のメールアドレスを設定）
  BOSS_EMAIL_SUBJECT（件名のプレフィックス）
"""

from __future__ import annotations

import os
import sys
import logging

import msal
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config
from app.collect.redmine import fetch_tickets, get_week_range
from app.collect.onenote import fetch_onenote_texts
from app.collect.outlook import fetch_pending_emails
from app.generate.report import generate_dashboard
from app.send.email import save_draft

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES_APP = ["https://graph.microsoft.com/.default"]


def get_access_token_app(config: Config) -> str:
    """Client Credentials Flow でアクセストークンを取得（GitHub Actions用）"""
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


def dashboard_to_text(dashboard: dict) -> str:
    """ダッシュボードデータをメール本文テキストに変換する"""
    lines = [f"【週次タスク整理ダッシュボード】対象週: {dashboard.get('weekLabel', '')}",  ""]

    todo = dashboard.get("todo", [])
    if todo:
        lines.append("■ 今週やること")
        for item in todo:
            priority = item.get("priority", "")
            text = item.get("text", "")
            lines.append(f"  [{priority}] {text}")
        lines.append("")

    delegate = dashboard.get("delegate", [])
    if delegate:
        lines.append("■ チームへの依頼候補")
        for item in delegate:
            lines.append(f"  ・{item.get('text', '')}")
        lines.append("")

    emails = dashboard.get("emails", [])
    if emails:
        lines.append("■ 対応が必要なメール")
        for item in emails:
            lines.append(f"  ・{item.get('text', '')}")
        lines.append("")

    tickets = dashboard.get("tickets", [])
    if tickets:
        lines.append("■ Redmine チケット")
        for item in tickets:
            lines.append(f"  ・{item.get('text', '')}")
        lines.append("")

    concerns = dashboard.get("concerns", [])
    if concerns:
        lines.append("■ 懸念事項")
        for c in concerns:
            lines.append(f"  ・{c}")
        lines.append("")

    lines.append("（このメールは自動生成されました）")
    return "\n".join(lines)


def main():
    config = Config()
    week_offset = int(os.getenv("WEEK_OFFSET", "0"))

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
    onenote_pages = fetch_onenote_texts(access_token, config)
    logger.info("OneNote: %d ページ", len(onenote_pages))

    logger.info("メール収集中...")
    emails = fetch_pending_emails(access_token, config)
    logger.info("メール: %d 件", len(emails))

    # AIタスク整理
    logger.info("AI でタスク整理中...")
    dashboard = generate_dashboard(config, tickets, onenote_pages, emails, week_label)

    # Outlook に下書き保存（宛先は自分のアドレス）
    report_text = dashboard_to_text(dashboard)
    subject_prefix = config.boss_email_subject or "タスク整理"
    # 自分宛に保存（BOSS_EMAIL を自分のアドレスに設定して使う）
    draft_id = save_draft(access_token, config, report_text)
    logger.info("Outlook 下書き保存完了: %s", draft_id)

    print("\n" + "=" * 60)
    print(f"タスク整理ダッシュボードを Outlook の下書きに保存しました")
    print(f"対象週: {week_label}")
    print("=" * 60)
    print(report_text)


if __name__ == "__main__":
    main()
