from pathlib import Path
import shutil
from fastapi import APIRouter, HTTPException
from backend.infrastructure.config import load_config, resolve_library_path, app_data_dir
from backend.infrastructure.database import database_path

router = APIRouter(prefix="/system", tags=["System"])

@router.get("/health")
def system_health():
    cfg = load_config()
    library = None
    library_ok = False
    try:
        library = str(resolve_library_path(cfg))
        library_ok = True
    except Exception:
        library = cfg.get("library_path") or None
    plugins = Path(cfg["plugins_path"])
    tools = {}
    for name in ("ffmpeg", "ffprobe", "mkvpropedit", "mkvmerge"):
        found = shutil.which(name) or shutil.which(name + ".exe")
        if not found and plugins.exists():
            candidates = list(plugins.rglob(name + ".exe"))
            found = str(candidates[0]) if candidates else None
        tools[name] = {"available": bool(found), "path": found}
    return {"status": "ok", "library_configured": library_ok, "library_path": library,
            "database_path": str(database_path()), "data_directory": str(app_data_dir()),
            "tools": tools}


@router.post("/select-folder")
def select_folder(payload: dict):
    """Open the native folder picker for the local desktop application."""
    title = str(payload.get("title") or "Seleccionar carpeta")[:120]
    initial_path = str(payload.get("initial_path") or "").strip()
    initial_directory = Path(initial_path).expanduser()
    if not initial_directory.is_dir():
        initial_directory = Path.home()

    root = None
    selected = ""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        selected = filedialog.askdirectory(
            parent=root,
            title=title,
            initialdir=str(initial_directory),
            mustexist=True,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="El selector nativo no está disponible. Comprueba que Python incluya soporte Tcl/Tk.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo abrir el selector de carpetas: {exc}",
        ) from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    if not selected:
        return {"selected": False, "path": None}

    selected_path = Path(selected).expanduser().resolve()
    if not selected_path.exists() or not selected_path.is_dir():
        raise HTTPException(status_code=422, detail="La ruta seleccionada no es una carpeta válida.")

    return {"selected": True, "path": str(selected_path)}
