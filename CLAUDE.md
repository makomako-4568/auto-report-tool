# auto-report-tool

面倒な報告を自動化するツール。

## プロジェクト構造

```
configs/
├── repos/           # リポジトリ・データソース定義
└── projects/        # プロジェクト別設定（投稿先、対象など）

.claude/
├── prompts/         # メイン処理フロー
└── skills/          # 各処理の詳細知識

output/              # 生成物の出力先
docs/                # ドキュメント
```

## 実行方法

```bash
claude "レポートを生成して"
```

## GitHub Secrets

| シークレット名 | 用途 |
|---------------|------|
| `ANTHROPIC_API_KEY` | Claude API認証 |
