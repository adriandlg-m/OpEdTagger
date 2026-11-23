from fastapi import APIRouter, HTTPException
from backend.api.schemas.models import TemplateSearchRequest, TemplateDownloadRequest
from backend.services.template_service import search_candidates, download_selected_templates
from backend.services.log_service import log_event

router = APIRouter(prefix="/templates", tags=["Templates"])

@router.post("/search")
def search_templates(body: TemplateSearchRequest):
    try:
        candidates = search_candidates(body.query.strip())
        return {"query": body.query, "total": len(candidates), "candidates": candidates}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AnimeThemes search failed: {exc}") from exc

@router.post("/download")
def download_templates(body: TemplateDownloadRequest):
    try:
        result = download_selected_templates(body.series_name.strip(), body.anime_slug.strip())
        log_event("INFO", "Templates", "AnimeThemes templates downloaded.", detail=str(result), series_name=body.series_name)
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
