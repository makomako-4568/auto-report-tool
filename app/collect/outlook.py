from __future__ import annotations

"""
Microsoft Graph API から対応が必要なメールを取得する
"""

import logging
from datetime import date, timedelta

import requests

from app.config import Config

logger = logging.getLogger(__name__)
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# 直近何日分のメールを対象にするか
EMAIL_RECENT_DAYS = 7

# 取得する最大件数
EMAIL_MAX = 20


def fetch_pending_emails(access_token: str, config: Config) -> list[dict]:
    """
    直近 EMAIL_RECENT_DAYS 日以内の未読または未返信のメールを取得する。

    Returns:
        [{"subject": str, "from": str, "receivedAt": str, "isRead": bool}, ...]
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    since = (date.today() - timedelta(days=EMAIL_RECENT_DAYS)).isoformat()

    params = {
        "$filter": f"receivedDateTime ge {since}T00:00:00Z and isRead eq false",
        "$orderby": "receivedDateTime desc",
        "$top": EMAIL_MAX,
        "$select": "id,subject,from,receivedDateTime,isRead,isDraft",
    }

    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/messages",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"メール取得エラー: {e}") from e

    messages = resp.json().get("value", [])

    result = []
    for msg in messages:
        if msg.get("isDraft"):
            continue
        result.append({
            "subject":    msg.get("subject", "（件名なし）"),
            "from":       msg.get("from", {}).get("emailAddress", {}).get("address", ""),
            "receivedAt": msg.get("receivedDateTime", "")[:10],
            "isRead":     msg.get("isRead", False),
        })

    logger.info("未読メール取得完了: %d 件", len(result))
    return result


def find_boss_email(access_token: str, config: Config) -> dict | None:
    """
    上司からの週次報告メールを最新1件取得する（後方互換用）。
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    filter_query = (
        f"from/emailAddress/address eq '{config.boss_email}'"
        f" and contains(subject, '{config.boss_email_subject}')"
    )
    params = {
        "$filter": filter_query,
        "$orderby": "receivedDateTime desc",
        "$top": 1,
        "$select": "id,subject,receivedDateTime,from,conversationId",
    }
    try:
        resp = requests.get(
            f"{GRAPH_BASE}/me/messages",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Outlook メール検索エラー: {e}") from e

    messages = resp.json().get("value", [])
    return messages[0] if messages else None
