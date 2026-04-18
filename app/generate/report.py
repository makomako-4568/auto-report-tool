"""
GitHub Models API（OpenAI 互換）を使ってタスク優先順位を整理する
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

import tiktoken
from openai import OpenAI

from app.config import Config

logger = logging.getLogger(__name__)

GITHUB_MODELS_BASE_URL = "https://models.inference.ai.azure.com"
MAX_DATA_TOKENS = 5000
ONENOTE_RECENT_DAYS = 7
MAX_TICKETS = 20


# ══════════════════════════════════════════
# 公開 API
# ══════════════════════════════════════════

def generate_dashboard(
    config: Config,
    tickets: list[dict],
    onenote_pages: list[dict],
    emails: list[dict],
    week_label: str,
) -> dict:
    """
    収集データからダッシュボード用の優先タスクリストを生成する。

    Returns:
        {
          "weekLabel": str,
          "todo": [{"priority": "高"|"中"|"低", "text": str}, ...],
          "delegate": [{"text": str}, ...],
          "emails": [{"text": str}, ...],
          "tickets": [{"text": str}, ...],
          "concerns": [str, ...]
        }
    """
    client = OpenAI(
        base_url=GITHUB_MODELS_BASE_URL,
        api_key=config.github_token,
    )

    filtered_tickets = _filter_tickets(tickets)
    filtered_pages   = _filter_onenote_pages(onenote_pages)
    data_text        = _build_data_text(filtered_tickets, filtered_pages, emails)

    logger.info(
        "タスク整理中: モデル=%s tickets=%d pages=%d emails=%d",
        config.ai_model, len(filtered_tickets), len(filtered_pages), len(emails),
    )

    response = client.chat.completions.create(
        model=config.ai_model,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user",   "content": _build_user_prompt(data_text, week_label)},
        ],
        temperature=0.3,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    logger.info("タスク整理完了: %d 文字", len(raw))

    result = _parse_dashboard(raw, week_label)

    # Redmine チケットは元データから直接追加（AIを通さず正確に）
    result["tickets"] = [
        {"text": f"#{t['id']} {t['subject']}（{t['status']} {t['done_ratio']}%・担当:{t['assignee']}）"}
        for t in filtered_tickets
    ]

    # メール一覧も元データから直接追加
    result["emails"] = [
        {"text": f"件名「{e['subject']}」（差出人:{e['from']} 受信:{e['receivedAt']}）"}
        for e in emails
    ]

    result["weekLabel"] = week_label
    return result


# ══════════════════════════════════════════
# データ絞り込み
# ══════════════════════════════════════════

def _filter_tickets(tickets: list[dict]) -> list[dict]:
    def priority(t: dict) -> int:
        r = t.get("done_ratio", 0)
        if 0 < r < 100: return 0
        if r == 100:    return 1
        return 2
    return sorted(tickets, key=priority)[:MAX_TICKETS]


def _filter_onenote_pages(pages: list[dict]) -> list[dict]:
    cutoff = date.today() - timedelta(days=ONENOTE_RECENT_DAYS)
    result = []
    for page in pages:
        pd = _parse_date_from_title(page.get("title", ""))
        if pd is None or pd >= cutoff:
            result.append(page)
    return result


def _parse_date_from_title(title: str) -> date | None:
    m = re.match(r"^(\d{1,2})/(\d{1,2})", title)
    if m:
        try: return date(date.today().year, int(m.group(1)), int(m.group(2)))
        except ValueError: pass
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", title)
    if m:
        try: return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError: pass
    return None


# ══════════════════════════════════════════
# データ → テキスト変換
# ══════════════════════════════════════════

def _build_data_text(
    tickets: list[dict],
    onenote_pages: list[dict],
    emails: list[dict],
) -> str:
    lines: list[str] = []

    if tickets:
        lines.append("<redmine_tickets>")
        for t in tickets:
            lines.append(
                f"  [{t['status']}] #{t['id']} {t['subject']}"
                f"（担当:{t['assignee']} 進捗:{t['done_ratio']}%）"
            )
        lines.append("</redmine_tickets>\n")

    if onenote_pages:
        lines.append("<onenote_pages>")
        for page in onenote_pages:
            lines.append(f"  <page section='{page['section']}' title='{page['title']}'>")
            lines.append(page["text"][:800])
            lines.append("  </page>")
        lines.append("</onenote_pages>\n")

    if emails:
        lines.append("<emails>")
        for e in emails:
            lines.append(f"  件名：{e['subject']}（差出人:{e['from']} 受信:{e['receivedAt']}）")
        lines.append("</emails>")

    full_text = "\n".join(lines)
    return _truncate_to_token_limit(full_text, MAX_DATA_TOKENS)


def _truncate_to_token_limit(text: str, max_tokens: int) -> str:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = enc.decode(tokens[:max_tokens])
        logger.warning("データをトークン上限で切り捨て: %d → %d tokens", len(tokens), max_tokens)
        return truncated + "\n（一部省略）"
    except Exception:
        return text[:max_tokens * 3]


# ══════════════════════════════════════════
# プロンプト（XMLタグで構造化）
# ══════════════════════════════════════════

def _system_prompt() -> str:
    return """\
<background>
あなたはチームリーダーの業務整理を支援するAIアシスタントです。
複数プロジェクトを抱えるチームリーダーが「今週自分が何をすべきか」を
素早く把握できるよう、収集データを整理・優先順位付けして提示します。
報告先は自分自身（チームリーダー本人）です。
</background>

<instructions>
- Redmineチケット・OneNoteメモ・メールから「今週やること」を抽出し、優先順位をつけること
- 優先度の判断基準：期限の近さ・進行中の作業の重要性・ブロッカーの有無
- 自分がやるべきことと、チーム員に依頼すべきことを分けて整理すること
- 依頼候補は担当者名を含めて具体的に記載すること
- 推測は含めず、データに記載された情報のみ使うこと
- 懸念事項があれば簡潔に記載すること
</instructions>

<output_format>
必ず以下の JSON 形式のみで出力すること。他の文字は含めないこと。

{
  "todo": [
    {"priority": "高", "text": "今週自分がやること（期限・チケット番号など含む）"},
    {"priority": "中", "text": "..."},
    {"priority": "低", "text": "..."}
  ],
  "delegate": [
    {"text": "チーム員名：依頼内容（チケット番号など含む）"}
  ],
  "concerns": [
    "懸念事項・注意点（なければ空配列 []）"
  ]
}
</output_format>

<example>
入力データ（Redmine 3件・OneNoteメモあり）に対する正しい出力例:

{
  "todo": [
    {"priority": "高", "text": "外部APIとの疎通確認（インフラ担当と連携、期限：今週金曜）"},
    {"priority": "高", "text": "チケット #1048 仕様書レビュー完了・先方へ回答"},
    {"priority": "中", "text": "月次コスト試算を経営会議前に更新（OneNote）"},
    {"priority": "低", "text": "本番リリース手順書レビュー（チケット #1047）"}
  ],
  "delegate": [
    {"text": "鈴木さん：バッチ処理リファクタリング実装着手（チケット #1043）"},
    {"text": "佐藤さん：本番リリース手順書ドラフト作成（チケット #1047）"}
  ],
  "concerns": [
    "外部APIの仕様に未確定箇所あり。先方の回答待ちのため進捗がブロックされるリスクあり。"
  ]
}
</example>
"""


def _build_user_prompt(data_text: str, week_label: str) -> str:
    return f"""\
<target_week>{week_label}</target_week>

<source_data>
{data_text}
</source_data>

上記データをもとに、<output_format> の JSON 形式で今週のタスクを整理してください。
"""


# ══════════════════════════════════════════
# JSON → ダッシュボードデータ変換
# ══════════════════════════════════════════

def _parse_dashboard(json_str: str, week_label: str) -> dict:
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("JSONパース失敗。空のダッシュボードを返します。")
        data = {}

    return {
        "weekLabel": week_label,
        "todo":      data.get("todo",     []),
        "delegate":  data.get("delegate", []),
        "emails":    [],
        "tickets":   [],
        "concerns":  data.get("concerns", []),
    }
