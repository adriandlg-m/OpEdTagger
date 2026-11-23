from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from backend.infrastructure.config import app_data_dir
from backend.services.job_service import job_service

router = APIRouter(prefix="/analysis", tags=["Analysis"])

@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_individual_analysis(file: UploadFile = File(...), series_name: str = Form(...), anime_slug: str | None = Form(default=None)):
    raw_filename = (file.filename or "episode.mkv").replace("\\", "/")
    filename = Path(raw_filename).name
    if Path(filename).suffix.lower() != ".mkv":
        raise HTTPException(status_code=415, detail="Only MKV files are supported.")
    series_name = series_name.strip()
    if not series_name or series_name in {".", ".."} or re.search(r"[\\/:\x00]", series_name):
        raise HTTPException(status_code=422, detail="Enter a valid anime title without path separators.")
    target_dir = app_data_dir() / "individual_uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    staged = target_dir / f"{uuid.uuid4().hex}_{filename}"
    size = 0
    max_size = 50 * 1024 * 1024 * 1024  # 50 GiB safety ceiling; streams to disk.
    try:
        with staged.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(status_code=413, detail="The uploaded MKV exceeds the 50 GiB limit.")
                destination.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        return job_service.create_individual_job(staged, series_name, anime_slug)
    except HTTPException:
        staged.unlink(missing_ok=True)
        raise
    except Exception as exc:
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
