"""
GitHub Models API（OpenAI 互換）を使ってレポートを生成する
"""

import logging

import tiktoken
from openai import OpenAI

from app.config import Config

logger = logging.getLogger(__name__)

# GitHub Models API のエンドポイント
GITHUB_MODELS_BASE_URL = "https://models.inference.ai.azure.com"

# プロンプトに含めるデータの最大トークン数（超えたら切り捨て）
MAX_DATA_TOKENS = 6000


def generate_report(
    config: Config,
    tickets: list[dict],
    onenote_pages: list[dict],
    week_label: str,
) -> str:
    """
    Redmine チケットと OneNote ページからレポート文章を生成する。

    Args:
        config: 設定オブジェクト
        tickets: Redmine チケット一覧
        onenote_pages: OneNote ページテキスト一覧
        week_label: 対象週ラベル（例: "2026/04/06〜04/12"）

    Returns:
        生成されたレポート文字列
    """
    client = OpenAI(
        base_url=GITHUB_MODELS_BASE_URL,
        api_key=config.github_token,
    )

    data_text = _build_data_text(tickets, onenote_pages)
    prompt = _build_prompt(data_text, week_label)

    logger.info("レポート生成中: モデル=%s", config.ai_model)
    response = client.chat.completions.create(
        model=config.ai_model,
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )

    report = response.choices[0].message.content or ""
    logger.info("レポート生成完了: %d 文字", len(report))
    return report.strip()


def _build_data_text(tickets: list[dict], onenote_pages: list[dict]) -> str:
    """収集データをテキストに変換し、トークン上限に収まるよう切り捨てる"""
    lines: list[str] = []

    # Redmine チケット
    if tickets:
        lines.append("## Redmine チケット（対象週に更新）")
        for t in tickets:
            lines.append(
                f"- [{t['status']}] {t['subject']} "
                f"(担当: {t['assignee']}, 進捗: {t['done_ratio']}%, "
                f"PJ: {t['project']})"
            )
        lines.append("")

    # OneNote ページ
    if onenote_pages:
        lines.append("## OneNote メモ")
        for page in onenote_pages:
            lines.append(f"### {page['notebook']} > {page['section']} > {page['title']}")
            lines.append(page["text"][:1000])  # 1ページ最大1000文字
            lines.append("")

    full_text = "\n".join(lines)

    # トークン数チェック・切り捨て
    return _truncate_to_token_limit(full_text, MAX_DATA_TOKENS)


def _truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """テキストを指定トークン数以内に切り捨てる"""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated = enc.decode(tokens[:max_tokens])
        logger.warning("データをトークン上限で切り捨てました: %d → %d tokens", len(tokens), max_tokens)
        return truncated + "\n\n（データが長いため一部省略されました）"
    except Exception:
        # tiktoken が失敗した場合は文字数で簡易切り捨て
        return text[:max_tokens * 3]


def _system_prompt() -> str:
    return """あなたは週次進捗報告の作成を補助するアシスタントです。
提供されたデータをもとに、上司への週次報告メールの本文を日本語で作成してください。

## 報告フォーマット
1. 今週の進捗（完了・対応済み事項）
2. 来週の予定・課題
3. 共有事項・懸念点（あれば）

## 注意事項
- 箇条書きを使い、簡潔にまとめること
- チームメンバー全員の動きを含めること
- 数値や具体的な成果を含めること
- ビジネスメールとして適切な文体を使うこと
- 署名・宛名は不要（テンプレートに含まれる）
"""


def _build_prompt(data_text: str, week_label: str) -> str:
    return f"""対象週: {week_label}

以下のデータをもとに週次報告を作成してください。

{data_text}
"""
