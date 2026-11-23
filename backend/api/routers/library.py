from fastapi import APIRouter, HTTPException
from backend.services.library_service import list_series, scan_library, get_series_detail

router = APIRouter(tags=["Library"])

@router.get("/library")
def library_index():
    try:
        return list_series(scan=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/library/scan")
def library_scan():
    try:
        scan_result = scan_library()
        return {**scan_result, **list_series(scan=False)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/series/{series_name}")
def series_detail(series_name: str):
    try:
        return get_series_detail(series_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
