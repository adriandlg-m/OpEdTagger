import json
from pathlib import Path

DATABASE_DIR = Path(__file__).parent.parent / "database"
MASTER_DB_FILE = DATABASE_DIR / "anime_master.json"

def load_master_db() -> dict:
    if MASTER_DB_FILE.exists():
        try:
            with MASTER_DB_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_master_db(db_data: dict):
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    with MASTER_DB_FILE.open("w", encoding="utf-8") as f:
        json.dump(db_data, f, indent=4, ensure_ascii=False)