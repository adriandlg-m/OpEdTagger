from pathlib import Path
import os
import subprocess
import sys
from fastapi import APIRouter, HTTPException
from backend.api.schemas.models import SettingsUpdate
from backend.infrastructure.config import load_config, save_config, app_data_dir
from core.backup_manager import ensure_backup_layout, get_backup_summary
from backend.infrastructure.database import db_session, utc_now
from backend.infrastructure.config import resolve_library_path

router = APIRouter(prefix="/settings", tags=["Settings"])

@router.get("")
def get_settings():
    cfg = load_config()
    return {"library_path": cfg.get("library_path", ""), "template_path": cfg["template_path"],
            "plugins_path": cfg["plugins_path"], "backup_path": cfg["backup_path"],
            "auto_download_templates": cfg.get("auto_download_templates", False),
            "database_path": cfg["database_path"], "data_directory": str(app_data_dir())}

@router.put("")
def update_settings(body: SettingsUpdate):
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "library_path" in changes:
        path = Path(changes["library_path"]).expanduser()
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=422, detail="The library path must already exist and be a directory.")
    try:
        cfg = save_config(changes)
        return {"library_path": cfg.get("library_path", ""), "template_path": cfg["template_path"],
                "plugins_path": cfg["plugins_path"], "backup_path": cfg["backup_path"],
                "auto_download_templates": cfg.get("auto_download_templates", False)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/preferences")
def get_preferences():
    with db_session() as conn:
        rows = conn.execute("SELECT key,value_json FROM app_settings").fetchall()
    import json
    return {row["key"]: json.loads(row["value_json"]) for row in rows}

@router.put("/preferences")
def save_preferences(payload: dict):
    import json
    allowed = {"theme", "confirm_injection", "show_outliers", "log_refresh"}
    if set(payload) - allowed:
        raise HTTPException(status_code=422, detail="Unsupported preference key.")
    with db_session() as conn:
        for key, value in payload.items():
            conn.execute("INSERT INTO app_settings(key,value_json,updated_at) VALUES(?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                         (key, json.dumps(value), utc_now()))
    return payload



@router.get("/backups")
def get_backups_summary():
    cfg = load_config()
    try:
        return get_backup_summary(Path(cfg["backup_path"]))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not inspect backup storage: {exc}") from exc


@router.post("/backups/open")
def open_backups_folder(payload: dict | None = None):
    cfg = load_config()
    section = str((payload or {}).get("section") or "root")
    directories = ensure_backup_layout(Path(cfg["backup_path"]))
    allowed = {
        "root": Path(cfg["backup_path"]).expanduser().resolve(),
        "temporary": directories["temporary"],
        "recovery": directories["recovery"],
        "chapters": directories["chapters"],
    }
    target = allowed.get(section)
    if target is None:
        raise HTTPException(status_code=422, detail="Unknown backup folder section.")
    try:
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open the backup folder: {exc}") from exc
    return {"opened": True, "path": str(target)}



@router.post("/backups/recovery/{transaction_id}/restore")
def restore_recovery_backup(transaction_id: str):
    from backend.services.chapter_service import restore_recovery_transaction

    try:
        return restore_recovery_transaction(transaction_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not restore the original MKV: {exc}") from exc
