from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from backend.services.log_service import list_logs

router = APIRouter(prefix="/logs", tags=["Logs"])

@router.get("")
def logs(level: str | None = Query(default=None), q: str | None = Query(default=None), limit: int = Query(default=300, ge=1, le=2000)):
    rows = list_logs(level, q, limit)
    return {"total": len(rows), "logs": rows}

@router.get("/export", response_class=PlainTextResponse)
def export_logs(level: str | None = None, q: str | None = None, limit: int = 2000):
    rows = list_logs(level, q, min(max(limit, 1), 2000))
    lines = [f"{row['timestamp']} [{row['level']}] {row['source']}: {row['message']}" + (f" | {row['detail']}" if row['detail'] else "") for row in rows]
    return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=opedtagger-logs.txt"})
