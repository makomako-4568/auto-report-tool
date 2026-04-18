"""
Microsoft Graph API 認証モジュール
Device Code Flow を使用（リダイレクトURI不要）
トークンはファイルキャッシュに永続化
"""

import json
import logging
from pathlib import Path

import msal

from app.config import Config, TOKEN_CACHE_FILE

logger = logging.getLogger(__name__)

# 最小権限スコープ
SCOPES = [
    "Notes.Read",        # OneNote 読み取り
    "Mail.Read",         # 上司メール検索（返信元ID取得）
    "Mail.Send",         # メール送信
]


def _load_cache() -> msal.SerializableTokenCache:
    """ファイルからトークンキャッシュを読み込む"""
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_FILE.exists():
        cache.deserialize(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    """変更があればトークンキャッシュをファイルに保存"""
    if cache.has_state_changed:
        TOKEN_CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")


def get_access_token(config: Config) -> str:
    """
    アクセストークンを取得する。
    キャッシュに有効なトークンがあればサイレント取得、
    なければ Device Code Flow で認証する。
    """
    cache = _load_cache()
    app = msal.PublicClientApplication(
        client_id=config.ms_client_id,
        authority=f"https://login.microsoftonline.com/{config.ms_tenant_id}",
        token_cache=cache,
    )

    # キャッシュからサイレント取得を試みる
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # キャッシュにない場合は Device Code Flow で認証
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device Code Flow の開始に失敗しました: {flow.get('error_description')}")

    print("\n" + "=" * 60)
    print("Microsoft 認証が必要です")
    print(f"  URL:  {flow['verification_uri']}")
    print(f"  コード: {flow['user_code']}")
    print("=" * 60 + "\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(
            f"認証に失敗しました: {result.get('error_description', result.get('error'))}"
        )

    _save_cache(cache)
    logger.info("Microsoft 認証成功")
    return result["access_token"]


def clear_token_cache() -> None:
    """保存済みトークンキャッシュを削除する（再認証が必要な場合）"""
    if TOKEN_CACHE_FILE.exists():
        TOKEN_CACHE_FILE.unlink()
        logger.info("トークンキャッシュを削除しました")
