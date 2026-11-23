from fastapi import APIRouter, HTTPException, Query
from backend.services.library_service import dashboard_summary
from backend.infrastructure.config import load_config

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("")
def get_dashboard(scan: bool = Query(default=False)):
    try:
        if not load_config().get("library_path"):
            return {"library_path": "", "total_series": 0, "total_episodes": 0, "analyzed_episodes": 0,
                    "pending_episodes": 0, "outliers": 0, "active_jobs": 0, "recent_jobs": [], "series": []}
        return dashboard_summary(scan=scan)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
