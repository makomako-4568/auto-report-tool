"""
エントリポイント
python app/main.py で Flask サーバーを起動する
"""

import webbrowser
import threading
import time

from app.config import Config
from app.factory import create_app


def main():
    config = Config()

    # 設定チェック（致命的なもののみ警告）
    errors = config.validate()
    if errors:
        print("\n⚠  設定が不足しています（.env ファイルを確認してください）")
        for e in errors:
            print(f"   - {e}")
        print()

    app = create_app(config)
    url = f"http://localhost:{config.flask_port}"

    # サーバー起動後にブラウザを自動オープン
    def open_browser():
        time.sleep(1.0)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n週次報告ツール 起動中 → {url}\n")
    app.run(
        host="127.0.0.1",
        port=config.flask_port,
        debug=config.flask_debug,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
