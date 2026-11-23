from pathlib import Path
from fastapi import APIRouter, HTTPException
from backend.api.schemas.models import SettingsUpdate
from backend.infrastructure.config import load_config, save_config, app_data_dir
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
