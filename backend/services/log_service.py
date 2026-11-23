from __future__ import annotations

from backend.infrastructure.database import db_session, utc_now


def log_event(level: str, source: str, message: str, detail: str | None = None,
              job_id: str | None = None, series_name: str | None = None,
              episode_name: str | None = None) -> None:
    normalized = level.upper()
    if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        normalized = "INFO"
    with db_session() as conn:
        conn.execute(
            "INSERT INTO activity_logs(timestamp,level,source,message,detail,job_id,series_name,episode_name) VALUES(?,?,?,?,?,?,?,?)",
            (utc_now(), normalized, source, message, detail, job_id, series_name, episode_name),
        )


def list_logs(level: str | None = None, query: str | None = None, limit: int = 200) -> list[dict]:
    clauses, params = [], []
    if level and level.upper() != "ALL":
        clauses.append("level = ?")
        params.append(level.upper())
    if query:
        clauses.append("(message LIKE ? OR source LIKE ? OR detail LIKE ? OR series_name LIKE ? OR episode_name LIKE ?)")
        pattern = f"%{query}%"
        params.extend([pattern] * 5)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_session() as conn:
        rows = conn.execute(
            f"SELECT * FROM activity_logs {where} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 2000))),
        ).fetchall()
    result = []
    for row in rows:
        result.append({
            "id": row["id"], "timestamp": row["timestamp"], "time": row["timestamp"][11:19] if row["timestamp"] else "",
            "level": row["level"], "source": row["source"], "message": row["message"],
            "detail": row["detail"], "job_id": row["job_id"], "series_name": row["series_name"],
            "episode_name": row["episode_name"],
        })
    return result
