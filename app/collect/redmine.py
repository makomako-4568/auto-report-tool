"""
Redmine API からチケット情報を取得する
"""

import logging
from datetime import date, timedelta

import requests

from app.config import Config

logger = logging.getLogger(__name__)

# 1回のAPIリクエストで取得する最大件数
PAGE_LIMIT = 100


def get_week_range(week_offset: int = 0) -> tuple[date, date]:
    """
    指定オフセット週の月曜〜日曜の日付範囲を返す。
    week_offset=0: 今週, -1: 先週, -2: 2週前
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def fetch_tickets(config: Config, week_offset: int = 0) -> list[dict]:
    """
    対象週に更新されたチケットを全プロジェクトから取得する。

    Returns:
        チケット情報のリスト（辞書形式）
    """
    if not config.redmine_projects:
        logger.warning("Redmine プロジェクトが未設定です")
        return []

    monday, sunday = get_week_range(week_offset)
    tickets: list[dict] = []

    headers = {"X-Redmine-API-Key": config.redmine_api_key}
    base_url = config.redmine_url

    for project in config.redmine_projects:
        logger.info("Redmine 取得中: プロジェクト=%s 期間=%s〜%s", project, monday, sunday)
        offset = 0

        while True:
            params = {
                "project_id": project,
                "updated_on": f"><{monday}|{sunday}",
                "status_id": "*",  # すべてのステータス
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            try:
                resp = requests.get(
                    f"{base_url}/issues.json",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
            except requests.RequestException as e:
                raise RuntimeError(f"Redmine API エラー (project={project}): {e}") from e

            data = resp.json()
            page_tickets = data.get("issues", [])
            tickets.extend(_normalize(t, project) for t in page_tickets)

            total = data.get("total_count", 0)
            offset += PAGE_LIMIT
            if offset >= total:
                break

        logger.info("Redmine 取得完了: プロジェクト=%s 件数=%d", project, len(tickets))

    return tickets


def _normalize(ticket: dict, project_name: str) -> dict:
    """APIレスポンスから必要なフィールドだけ抽出して返す"""
    return {
        "id": ticket.get("id"),
        "project": project_name,
        "subject": ticket.get("subject", ""),
        "status": ticket.get("status", {}).get("name", ""),
        "assignee": ticket.get("assigned_to", {}).get("name", "未割り当て"),
        "updated_on": ticket.get("updated_on", "")[:10],
        "done_ratio": ticket.get("done_ratio", 0),
        "description": (ticket.get("description") or "")[:200],
    }
