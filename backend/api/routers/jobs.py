from fastapi import APIRouter, HTTPException
from backend.api.schemas.models import BatchInjectionRequest, SeriesJobRequest
from backend.services.job_service import job_service

router = APIRouter(prefix="/jobs", tags=["Jobs"])

@router.get("")
def list_jobs(limit: int = 100):
    jobs = job_service.list_jobs(limit)
    return {"total": len(jobs), "jobs": jobs}

@router.post("/series/{series_name}", status_code=202)
def start_series_job(series_name: str, body: SeriesJobRequest | None = None):
    body = body or SeriesJobRequest()
    if any(i < 0 for i in body.skip_indices):
        raise HTTPException(status_code=422, detail="skip_indices must contain non-negative integers.")
    try:
        return job_service.create_series_job(series_name, set(body.skip_indices), body.selected_anime_slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job

@router.post("/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str):
    if not job_service.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job is not active or cannot be cancelled.")
    return {"job_id": job_id, "detail": "Cancellation requested."}


@router.post("/series/{series_name}/inject", status_code=202)
def start_batch_injection(series_name: str, body: BatchInjectionRequest):
    try:
        return job_service.create_injection_job(
            series_name,
            body.episode_ids,
            replace_existing_chapters=body.replace_existing_chapters,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
