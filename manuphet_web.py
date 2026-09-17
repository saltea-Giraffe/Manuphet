"""
manuphet_web.py  –  Manuphet Web エントリーポイント

責務:
  - FastAPI アプリを生成してルーターをマウント
  - サービスインスタンスをルーターにバインド
  - python manuphet_web.py 直実行 or run_web.py から uvicorn で起動

設定は config.py を参照（MANUPHET_* 環境変数 / settings.json）。
"""
from __future__ import annotations

from fastapi import FastAPI

import config
from api.routes import VERSION, bind_service, router
from api.service import ManuphetService

svc = ManuphetService()
bind_service(svc)

app = FastAPI(title=f"{config.APP_NAME} Web", version=VERSION)
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, reload=False, log_level="info")
