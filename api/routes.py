"""
api/routes.py  –  FastAPI ルーター定義

サービスインスタンスは manuphet_web.py で生成し、bind_service() で差し込む。
画面は web/index.html（1ファイルの SPA）を配信する。
"""
from __future__ import annotations

import html
import logging
import traceback
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
from api.service import ManuphetService

logger = logging.getLogger(__name__)
router = APIRouter()

_WEB_DIR = config.resource_dir() / "web"
_ASSETS_DIR = config.resource_dir() / "assets"
_ASSET_FILES = {"manuphet.png", "manuphet_64.png", "manuphet.ico"}


def _read_version() -> str:
    try:
        return (config.resource_dir() / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


VERSION = _read_version()

# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------

class ItemRequest(BaseModel):
    item_code: str

class ExclusionRequest(BaseModel):
    item_code: str
    excluded: bool

class WeeklyRequest(BaseModel):
    item_code: str
    weekly: bool

class EmailRequest(BaseModel):
    email: str

class SmtpConfigRequest(BaseModel):
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str = ""
    from_addr: str = ""
    password: str | None = None

class NotifySettingsRequest(BaseModel):
    enabled: bool | None = None

class NotifyRunRequest(BaseModel):
    force: bool = False

class DemandRequest(BaseModel):
    record_date: str
    item_code: str
    quantity: float
    customer: str | None = None

class InventoryRequest(BaseModel):
    code: str
    quantity: float

class BomRequest(BaseModel):
    item_code: str
    component_code: str
    usage_per_unit: float | None = 1.0

class CsvImportRequest(BaseModel):
    target: str
    csv_text: str
    mode: str = "append"
    source_name: str = ""

class ClearRequest(BaseModel):
    target: str
    confirm: bool = False

class MySQLConfigRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = ""
    database: str = ""
    driver: str = "mysqlconnector"
    password: str | None = None

class FilterModel(BaseModel):
    column: str
    op: str = "="
    value: Any = None

class MySQLPreviewRequest(BaseModel):
    table: str
    target: str | None = None
    mapping: dict[str, str] | None = None
    filters: list[FilterModel] = Field(default_factory=list)
    limit: int = 20

class MySQLImportRequest(BaseModel):
    table: str
    target: str
    mapping: dict[str, str]
    filters: list[FilterModel] = Field(default_factory=list)
    mode: str = "replace_source"
    save_as: str | None = None


# ---------------------------------------------------------------------------
# サービス参照（アプリ起動時に manuphet_web.py が差し込む）
# ---------------------------------------------------------------------------
_svc: ManuphetService | None = None


def bind_service(svc: ManuphetService) -> None:
    global _svc
    _svc = svc


def _s() -> ManuphetService:
    if _svc is None:
        raise RuntimeError("Service not initialized")
    return _svc


def _call(fn: Callable[[], Any], label: str) -> Any:
    """ValueError は 400、それ以外は 500 として返す。"""
    try:
        return fn()
    except HTTPException:
        raise
    except (ValueError, FileNotFoundError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[%s] %s\n%s", label, e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


def _filters(items: list[FilterModel]) -> list[dict[str, Any]]:
    return [f.model_dump() for f in items]


# ---------------------------------------------------------------------------
# 状態 / 再読込
# ---------------------------------------------------------------------------

@router.get("/api/status")
def status() -> dict[str, Any]:
    return _call(lambda: {**_s().get_status(), "version": VERSION}, "status")


@router.post("/api/refresh")
def refresh() -> dict[str, Any]:
    _call(_s().refresh_db, "refresh")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 品目・学習・予測
# ---------------------------------------------------------------------------

@router.get("/api/items")
def items(search: str = "", include_excluded: bool = False) -> dict[str, Any]:
    return {"items": _call(lambda: _s().list_items(search=search, include_excluded=include_excluded), "items")}


@router.post("/api/train")
def train(req: ItemRequest) -> dict[str, Any]:
    if not config.ALLOW_WEB_TRAIN:
        raise HTTPException(status_code=403, detail="Web からの学習は無効に設定されています")
    return _call(lambda: _s().train_one(req.item_code.strip()), "train")


@router.post("/api/train_all")
def train_all() -> dict[str, Any]:
    if not config.ALLOW_WEB_TRAIN:
        raise HTTPException(status_code=403, detail="Web からの学習は無効に設定されています")
    return _call(_s().train_all, "train_all")


@router.get("/api/train_status")
def train_status() -> dict[str, Any]:
    return _call(_s().get_train_status, "train_status")


@router.get("/api/forecast_plot")
def forecast_plot(item_code: str) -> StreamingResponse:
    buf = _call(lambda: _s().backtest_png(item_code.strip()), "forecast_plot")
    return StreamingResponse(buf, media_type="image/png")


@router.get("/api/supply")
def supply(item_code: str) -> dict[str, Any]:
    return _call(lambda: _s().supply_analysis(item_code.strip()), "supply")


# ---------------------------------------------------------------------------
# 品目設定（除外 / 週次）
# ---------------------------------------------------------------------------

@router.get("/api/excluded")
def excluded() -> dict[str, Any]:
    return {"excluded": sorted(_s().get_excluded_set())}


@router.post("/api/excluded")
def update_excluded(req: ExclusionRequest) -> dict[str, Any]:
    _call(lambda: _s().set_excluded(req.item_code.strip(), req.excluded), "excluded")
    return {"ok": True}


@router.get("/api/weekly")
def weekly() -> dict[str, Any]:
    return {"weekly": sorted(_s().get_weekly_set())}


@router.post("/api/weekly")
def update_weekly(req: WeeklyRequest) -> dict[str, Any]:
    _call(lambda: _s().set_weekly(req.item_code.strip(), req.weekly), "weekly")
    return {"ok": True}


# ---------------------------------------------------------------------------
# データ入力
# ---------------------------------------------------------------------------

@router.get("/api/data/meta")
def data_meta() -> dict[str, Any]:
    return ManuphetService.import_meta()


@router.get("/api/data/summary")
def data_summary() -> dict[str, Any]:
    return _call(_s().store.summary, "data_summary")


@router.get("/api/data/demand")
def list_demand(item_code: str = "", search: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return _call(lambda: _s().store.list_demand(item_code, search, min(limit, 1000), offset), "list_demand")


@router.post("/api/data/demand")
def add_demand(req: DemandRequest) -> dict[str, Any]:
    new_id = _call(lambda: _s().add_demand(req.record_date, req.item_code, req.quantity, req.customer),
                   "add_demand")
    return {"ok": True, "id": new_id}


@router.delete("/api/data/demand/{record_id}")
def delete_demand(record_id: int) -> dict[str, Any]:
    return {"ok": _call(lambda: _s().delete_demand(record_id), "delete_demand")}


@router.get("/api/data/inventory")
def list_inventory(search: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return _call(lambda: _s().store.list_inventory(search, min(limit, 1000), offset), "list_inventory")


@router.post("/api/data/inventory")
def upsert_inventory(req: InventoryRequest) -> dict[str, Any]:
    _call(lambda: _s().upsert_inventory(req.code, req.quantity), "upsert_inventory")
    return {"ok": True}


@router.delete("/api/data/inventory")
def delete_inventory(code: str) -> dict[str, Any]:
    return {"ok": _call(lambda: _s().store.delete_inventory(code), "delete_inventory")}


@router.get("/api/data/bom")
def list_bom(item_code: str = "", search: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return _call(lambda: _s().store.list_bom(item_code, search, min(limit, 1000), offset), "list_bom")


@router.post("/api/data/bom")
def upsert_bom(req: BomRequest) -> dict[str, Any]:
    _call(lambda: _s().upsert_bom(req.item_code, req.component_code, req.usage_per_unit), "upsert_bom")
    return {"ok": True}


@router.delete("/api/data/bom/{bom_id}")
def delete_bom(bom_id: int) -> dict[str, Any]:
    return {"ok": _call(lambda: _s().store.delete_bom(bom_id), "delete_bom")}


@router.post("/api/data/import_csv")
def import_csv(req: CsvImportRequest) -> dict[str, Any]:
    result = _call(lambda: _s().import_csv(req.target, req.csv_text, req.mode, req.source_name), "import_csv")
    return {"ok": True, **result}


@router.post("/api/data/load_sample")
def load_sample() -> dict[str, Any]:
    return {"ok": True, "result": _call(_s().load_sample, "load_sample")}


@router.post("/api/data/clear")
def clear_data(req: ClearRequest) -> dict[str, Any]:
    if not req.confirm:
        raise HTTPException(status_code=400, detail="confirm=true を指定してください")
    return {"ok": True, "deleted": _call(lambda: _s().clear_data(req.target), "clear_data")}


# ---------------------------------------------------------------------------
# MySQL 連携
# ---------------------------------------------------------------------------

@router.get("/api/mysql/config")
def mysql_config() -> dict[str, Any]:
    return _s().get_mysql_config()


@router.post("/api/mysql/config")
def save_mysql_config(req: MySQLConfigRequest) -> dict[str, Any]:
    _call(lambda: _s().save_mysql_config(req.host.strip(), req.port, req.user.strip(),
                                         req.database.strip(), req.driver, req.password), "mysql_config")
    return {"ok": True}


@router.post("/api/mysql/test")
def mysql_test() -> dict[str, Any]:
    return _call(_s().mysql_test, "mysql_test")


@router.get("/api/mysql/tables")
def mysql_tables() -> dict[str, Any]:
    return {"tables": _call(_s().mysql_tables, "mysql_tables")}


@router.get("/api/mysql/columns")
def mysql_columns(table: str) -> dict[str, Any]:
    return {"columns": _call(lambda: _s().mysql_columns(table), "mysql_columns")}


@router.post("/api/mysql/preview")
def mysql_preview(req: MySQLPreviewRequest) -> dict[str, Any]:
    return _call(lambda: _s().mysql_preview(req.table, req.target, req.mapping,
                                            _filters(req.filters), req.limit), "mysql_preview")


@router.post("/api/mysql/import")
def mysql_import(req: MySQLImportRequest) -> dict[str, Any]:
    return _call(lambda: _s().mysql_import(req.table, req.target, req.mapping, _filters(req.filters),
                                           req.mode, (req.save_as or "").strip() or None), "mysql_import")


@router.get("/api/mysql/jobs")
def mysql_jobs() -> dict[str, Any]:
    return {"jobs": _call(_s().mysql_jobs, "mysql_jobs")}


@router.post("/api/mysql/jobs/{job_id}/run")
def mysql_run_job(job_id: int) -> dict[str, Any]:
    return _call(lambda: _s().mysql_run_job(job_id), "mysql_run_job")


@router.delete("/api/mysql/jobs/{job_id}")
def mysql_delete_job(job_id: int) -> dict[str, Any]:
    return {"ok": _call(lambda: _s().mysql_delete_job(job_id), "mysql_delete_job")}


@router.post("/api/mysql/sync_all")
def mysql_sync_all() -> dict[str, Any]:
    return _call(_s().mysql_sync_all, "mysql_sync_all")


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------

@router.get("/api/emails")
def emails() -> dict[str, Any]:
    return {"emails": _s().get_email_list()}


@router.post("/api/emails")
def add_email(req: EmailRequest) -> dict[str, Any]:
    _call(lambda: _s().add_email(req.email), "add_email")
    return {"ok": True}


@router.delete("/api/emails")
def remove_email(req: EmailRequest) -> dict[str, Any]:
    _call(lambda: _s().remove_email(req.email.strip()), "remove_email")
    return {"ok": True}


@router.get("/api/smtp_config")
def smtp_config() -> dict[str, Any]:
    return _call(_s().get_smtp_config, "smtp_config")


@router.post("/api/smtp_config")
def save_smtp_config(req: SmtpConfigRequest) -> dict[str, Any]:
    _call(lambda: _s().save_smtp_config(
        smtp_server=req.smtp_server.strip(),
        smtp_port=req.smtp_port,
        username=req.username.strip(),
        from_addr=req.from_addr.strip(),
        password=req.password,
    ), "save_smtp_config")
    return {"ok": True}


@router.post("/api/smtp_test")
def smtp_test() -> dict[str, Any]:
    return _call(_s().send_test_email, "smtp_test")


@router.get("/api/notify_settings")
def notify_settings() -> dict[str, Any]:
    return _call(_s().get_notify_settings, "notify_settings")


@router.post("/api/notify_settings")
def update_notify_settings(req: NotifySettingsRequest) -> dict[str, Any]:
    return _call(lambda: _s().update_notify_settings(req.enabled), "update_notify_settings")


@router.post("/api/notify_run")
def notify_run(req: NotifyRunRequest) -> dict[str, Any]:
    return _call(lambda: _s().run_notification(force=bool(req.force)), "notify_run")


# ---------------------------------------------------------------------------
# 画面・静的ファイル
# ---------------------------------------------------------------------------

def _nav_links_html() -> str:
    parts = []
    for link in config.NAV_LINKS:
        url = str(link.get("url", ""))
        if not url.lower().startswith(("http://", "https://")):
            continue
        parts.append(
            f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">'
            f'{html.escape(str(link.get("label", url)))}</a>'
        )
    return "\n".join(parts)


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    page = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
    return (page
            .replace("<!-- NAV_LINKS -->", _nav_links_html())
            .replace("<!-- VERSION -->", html.escape(VERSION))
            .replace("<!-- APP_NAME -->", config.APP_NAME))


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "manuphet.ico", media_type="image/x-icon")


@router.get("/assets/{name}", include_in_schema=False)
def asset(name: str) -> FileResponse:
    if name not in _ASSET_FILES:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(_ASSETS_DIR / name)
