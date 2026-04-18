from __future__ import annotations

import logging
import urllib.parse

import requests

from app.config import Config

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def find_boss_email(access_token: str, config: Config) -> dict | None:
    """
    上司からの週次報告メールを最新1件取得する。
    返信時のスレッドID確認に使用。

    Returns:
        メッセージ辞書 または None（見つからない場合）
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    # 件名の部分一致でフィルタ（OData クエリ）
    subject_filter = urllib.parse.quote(config.boss_email_subject)
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
    if not messages:
        logger.info("上司からの週次報告メールが見つかりませんでした")
        return None

    msg = messages[0]
    logger.info(
        "上司メール発見: subject=%s received=%s",
        msg.get("subject"),
        msg.get("receivedDateTime"),
    )
    return msg
