"""
GitHub Models API（OpenAI 互換）を使ってレポートを生成する

改善点:
  ① プロンプトをXMLタグで構造化（background / instructions / output_format / example）
  ③ データ絞り込み（進捗・重要度でソート、直近7日のOneNoteのみ）
  ④ JSON出力固定 → テンプレートで整形（ブレをコードで止める）
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import tiktoken
from openai import OpenAI

from app.config import Config

logger = logging.getLogger(__name__)

GITHUB_MODELS_BASE_URL = "https://models.inference.ai.azure.com"

# データ部分のトークン上限
MAX_DATA_TOKENS = 5000

# OneNote は直近何日分を使うか
ONENOTE_RECENT_DAYS = 7

# Redmine チケット最大件数（絞り込み後）
MAX_TICKETS = 20


# ══════════════════════════════════════════
# 公開 API
# ══════════════════════════════════════════

def generate_report(
    config: Config,
    tickets: list[dict],
    onenote_pages: list[dict],
    week_label: str,
) -> str:
    """
    Redmine チケットと OneNote ページからレポート文章を生成する。

    Returns:
        整形済みレポート文字列（人間が読めるプレーンテキスト）
    """
    client = OpenAI(
        base_url=GITHUB_MODELS_BASE_URL,
        api_key=config.github_token,
    )

    # ③ データ絞り込み
    filtered_tickets = _filter_tickets(tickets)
    filtered_pages   = _filter_onenote_pages(onenote_pages)

    data_text = _build_data_text(filtered_tickets, filtered_pages)
    user_prompt = _build_user_prompt(data_text, week_label)

    logger.info(
        "レポート生成中: モデル=%s tickets=%d pages=%d",
        config.ai_model, len(filtered_tickets), len(filtered_pages),
    )

    # ④ JSON モードで出力を固定
    response = client.chat.completions.create(
        model=config.ai_model,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    logger.info("レポート生成完了: %d 文字", len(raw))

    # ④ JSON → テンプレートで整形
    return _format_report(raw, week_label)


# ══════════════════════════════════════════
# ③ データ絞り込み
# ══════════════════════════════════════════

def _filter_tickets(tickets: list[dict]) -> list[dict]:
    """
    Redmine チケットを重要度順にソートし、上位 MAX_TICKETS 件に絞る。

    優先順位:
      1. 進行中（done_ratio > 0 かつ < 100）
      2. 完了（done_ratio == 100）
      3. 未着手（done_ratio == 0）
    """
    def priority(t: dict) -> int:
        r = t.get("done_ratio", 0)
        if 0 < r < 100:
            return 0   # 進行中を最優先
        if r == 100:
            return 1   # 完了
        return 2       # 未着手

    sorted_tickets = sorted(tickets, key=priority)
    if len(sorted_tickets) > MAX_TICKETS:
        logger.info("チケットを %d 件 → %d 件に絞り込みました", len(sorted_tickets), MAX_TICKETS)
    return sorted_tickets[:MAX_TICKETS]


def _filter_onenote_pages(pages: list[dict]) -> list[dict]:
    """
    OneNote ページを直近 ONENOTE_RECENT_DAYS 日分に絞る。
    日付情報がないページは常に含める。
    """
    cutoff = date.today() - timedelta(days=ONENOTE_RECENT_DAYS)
    result = []
    for page in pages:
        title = page.get("title", "")
        # タイトルが「04/07 (月)」形式の日報ページは日付で絞り込む
        page_date = _parse_date_from_title(title)
        if page_date is None or page_date >= cutoff:
            result.append(page)
    if len(result) < len(pages):
        logger.info("OneNoteページを %d 件 → %d 件に絞り込みました", len(pages), len(result))
    return result


def _parse_date_from_title(title: str) -> date | None:
    """
    「04/07 (月)」「2026/04/07」形式のタイトルから date を返す。
    パースできない場合は None。
    """
    import re
    # MM/DD 形式
    m = re.match(r"^(\d{1,2})/(\d{1,2})", title)
    if m:
        try:
            today = date.today()
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    # YYYY/MM/DD 形式
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", title)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ══════════════════════════════════════════
# データ → テキスト変換
# ══════════════════════════════════════════

def _build_data_text(tickets: list[dict], onenote_pages: list[dict]) -> str:
    """絞り込み済みデータをテキストに変換し、トークン上限内に収める"""
    lines: list[str] = []

    if tickets:
        lines.append("<redmine_tickets>")
        for t in tickets:
            lines.append(
                f"  [{t['status']}] {t['subject']} "
                f"(担当:{t['assignee']} 進捗:{t['done_ratio']}% PJ:{t['project']})"
            )
        lines.append("</redmine_tickets>")
        lines.append("")

    if onenote_pages:
        lines.append("<onenote_pages>")
        for page in onenote_pages:
            lines.append(
                f"  <page notebook='{page['notebook']}' "
                f"section='{page['section']}' title='{page['title']}'>"
            )
            lines.append(page["text"][:800])   # 1ページ最大800文字
            lines.append("  </page>")
        lines.append("</onenote_pages>")

    full_text = "\n".join(lines)
    return _truncate_to_token_limit(full_text, MAX_DATA_TOKENS)


def _truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """テキストを指定トークン数以内に切り捨てる"""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = enc.decode(tokens[:max_tokens])
        logger.warning("データをトークン上限で切り捨て: %d → %d tokens", len(tokens), max_tokens)
        return truncated + "\n（データが長いため一部省略）"
    except Exception:
        return text[:max_tokens * 3]


# ══════════════════════════════════════════
# ① プロンプト構造化（XMLタグ）
# ══════════════════════════════════════════

def _system_prompt() -> str:
    return """\
<background>
あなたは週次進捗報告の作成を補助するアシスタントです。
チームリーダーが上司へ送る社内の週次報告メールの本文を生成します。
報告先は部長クラスの上司であり、簡潔・明確・丁寧な文体が求められます。
</background>

<instructions>
- 提供された Redmine チケットおよび OneNote メモをもとに報告文を作成してください
- チームメンバー全員の動きを含めること
- 進行中の課題には具体的な進捗率や次のアクションを記載すること
- 完了事項は成果が伝わるよう端的に記載すること
- 数値・固有名詞・チケット番号は正確に使うこと
- 署名・宛名・件名は不要（メールテンプレートに含まれる）
- データに記載がない推測は含めないこと
</instructions>

<output_format>
必ず以下の JSON 形式で出力してください。他の文字列は一切含めないこと。

{
  "greeting": "お疲れ様です。今週の進捗をご報告いたします。",
  "completed": [
    "完了事項を箇条書きで（1行1項目）"
  ],
  "in_progress": [
    "進行中事項を箇条書きで（進捗率や次のアクションを含む）"
  ],
  "next_week": [
    "来週の予定を箇条書きで"
  ],
  "concerns": [
    "懸念点・共有事項を箇条書きで（なければ空配列 []）"
  ]
}
</output_format>

<example>
入力データ例（Redmine チケット2件・OneNoteメモあり）に対する正しい出力例:

{
  "greeting": "お疲れ様です。今週の進捗をご報告いたします。",
  "completed": [
    "APIエラー調査・修正（チケット #1042）：本番環境へのデプロイまで完了（担当：田中）",
    "単体テスト追加・PR マージ（チケット #1045）：テストカバレッジが 72% → 85% に向上（担当：山田）"
  ],
  "in_progress": [
    "バッチ処理リファクタリング（チケット #1043）：設計レビュー完了、来週より実装フェーズへ移行予定（担当：鈴木）",
    "外部連携API仕様書レビュー対応（チケット #1048）：差分確認まで完了、先方への確認事項をまとめ中（担当：田中・山田）"
  ],
  "next_week": [
    "外部APIとの疎通確認（インフラ担当と連携）",
    "バッチ処理リファクタリング 実装着手",
    "本番リリース手順書のドラフト完成（チケット #1047）"
  ],
  "concerns": [
    "外部連携APIの仕様に未確定箇所が残っており、先方の回答待ちです"
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

上記データをもとに、<output_format> の JSON 形式で週次報告を作成してください。
"""


# ══════════════════════════════════════════
# ④ JSON → テンプレート整形
# ══════════════════════════════════════════

def _format_report(json_str: str, week_label: str) -> str:
    """
    AIが返した JSON をパースして、読みやすいプレーンテキストに整形する。
    パース失敗時は JSON をそのまま返す（フォールバック）。
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("JSONパース失敗。生テキストをそのまま返します。")
        return json_str

    lines: list[str] = []

    greeting = data.get("greeting", "")
    if greeting:
        lines.append(greeting)
        lines.append("")

    completed = data.get("completed", [])
    if completed:
        lines.append("【今週の進捗】")
        lines.append("■ 完了事項")
        for item in completed:
            lines.append(f"・{item}")
        lines.append("")

    in_progress = data.get("in_progress", [])
    if in_progress:
        lines.append("■ 進行中")
        for item in in_progress:
            lines.append(f"・{item}")
        lines.append("")

    next_week = data.get("next_week", [])
    if next_week:
        lines.append("【来週の予定】")
        for item in next_week:
            lines.append(f"・{item}")
        lines.append("")

    concerns = data.get("concerns", [])
    if concerns:
        lines.append("【課題・懸念事項】")
        for item in concerns:
            lines.append(f"・{item}")
        lines.append("")

    lines.append("以上、よろしくお願いいたします。")

    return "\n".join(lines)
