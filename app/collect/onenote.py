"""
Microsoft Graph API から OneNote ページを取得してテキスト変換する
"""

import logging
import re

import requests
from bs4 import BeautifulSoup

from app.config import Config

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def fetch_onenote_texts(access_token: str, config: Config, week_offset: int = 0) -> list[dict]:
    """
    設定で指定したノートブック・セクションのページを取得し、
    テキスト変換した結果をリストで返す。

    Returns:
        [{"notebook": str, "section": str, "title": str, "text": str}, ...]
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    results: list[dict] = []

    # ノートブック一覧を取得してIDをマッピング
    notebook_ids = _get_notebook_ids(headers, config.onenote_notebooks)

    for nb_name, nb_id in notebook_ids.items():
        # セクション一覧を取得してIDをマッピング
        section_ids = _get_section_ids(headers, nb_id, config.onenote_sections)

        for sec_name, sec_id in section_ids.items():
            logger.info("OneNote 取得中: %s > %s", nb_name, sec_name)
            pages = _get_pages(headers, sec_id)

            for page in pages:
                content = _get_page_content(headers, page["id"])
                text = _html_to_text(content)
                results.append({
                    "notebook": nb_name,
                    "section": sec_name,
                    "title": page.get("title", "（無題）"),
                    "text": text,
                })

    logger.info("OneNote 取得完了: %d ページ", len(results))
    return results


def _get_notebook_ids(headers: dict, target_names: list[str]) -> dict[str, str]:
    """ノートブック名 → ID のマッピングを返す"""
    resp = requests.get(f"{GRAPH_BASE}/me/onenote/notebooks", headers=headers, timeout=30)
    resp.raise_for_status()
    notebooks = resp.json().get("value", [])
    return {
        nb["displayName"]: nb["id"]
        for nb in notebooks
        if nb["displayName"] in target_names
    }


def _get_section_ids(headers: dict, notebook_id: str, target_names: list[str]) -> dict[str, str]:
    """セクション名 → ID のマッピングを返す"""
    resp = requests.get(
        f"{GRAPH_BASE}/me/onenote/notebooks/{notebook_id}/sections",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    sections = resp.json().get("value", [])
    return {
        sec["displayName"]: sec["id"]
        for sec in sections
        if sec["displayName"] in target_names
    }


def _get_pages(headers: dict, section_id: str) -> list[dict]:
    """セクション内のページ一覧を返す（最新50件）"""
    resp = requests.get(
        f"{GRAPH_BASE}/me/onenote/sections/{section_id}/pages",
        headers=headers,
        params={"$top": 50, "$orderby": "lastModifiedDateTime desc"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


def _get_page_content(headers: dict, page_id: str) -> str:
    """ページの HTML 本文を取得する"""
    resp = requests.get(
        f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def _html_to_text(html: str) -> str:
    """OneNote の HTML をプレーンテキストに変換する"""
    soup = BeautifulSoup(html, "lxml")

    # 不要なタグ（スクリプト・スタイル・画像）を除去
    for tag in soup(["script", "style", "img", "object"]):
        tag.decompose()

    # リスト項目に記号を付加
    for li in soup.find_all("li"):
        li.insert_before("• ")

    text = soup.get_text(separator="\n")

    # 連続する空行を最大2行に圧縮
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
