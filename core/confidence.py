from __future__ import annotations

import math
from typing import Any

CONFIDENCE_MODEL_VERSION = "heuristic-confidence-v1"
HIGH_CONFIDENCE_THRESHOLD = 90
MEDIUM_CONFIDENCE_THRESHOLD = 70


def _unit_interval(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


def assess_segment(segment: dict | None, *, temporal_consistency: float | None = None,
                   anomalous: bool = False) -> dict | None:
    """Attach an explainable 0-100 heuristic confidence index to a detection.

    This is a ranking/triage aid, not a calibrated probability of correctness.
    """
    if segment is None:
        return None
    result = dict(segment)
    similarity = _unit_interval(result.get("similarity_score"))
    prominence = _unit_interval(result.get("peak_prominence"))
    consistency = _unit_interval(temporal_consistency)

    if similarity is None or prominence is None:
        result.update({
            "confidence_score": None,
            "confidence_band": "UNASSESSED",
            "confidence_factors": {
                "similarity": similarity,
                "peak_clarity": prominence,
                "temporal_consistency": consistency,
                "model_version": CONFIDENCE_MODEL_VERSION,
                "reasons": [
                    "Faltan métricas normalizadas suficientes para evaluar esta detección.",
                    "Es necesario revisar o volver a analizar este segmento.",
                ],
            },
        })
        return result

    if consistency is None:
        score_value = 0.65 * similarity + 0.35 * prominence
    else:
        score_value = 0.50 * similarity + 0.25 * prominence + 0.25 * consistency
    if anomalous:
        score_value = min(score_value, 0.69)

    score = int(round(max(0.0, min(1.0, score_value)) * 100))
    band = "HIGH" if score >= HIGH_CONFIDENCE_THRESHOLD else (
        "MEDIUM" if score >= MEDIUM_CONFIDENCE_THRESHOLD else "LOW"
    )
    reasons = [
        f"Similitud normalizada: {similarity:.3f}.",
        f"Claridad del pico: {prominence:.3f}.",
    ]
    if consistency is not None:
        reasons.append(f"Consistencia temporal: {consistency:.3f}.")
    else:
        reasons.append("Aún no hay suficientes episodios comparables para medir consistencia temporal.")
    if anomalous:
        reasons.append("El instante se desvía del patrón del mismo template en otros episodios.")

    result.update({
        "confidence_score": score,
        "confidence_band": band,
        "confidence_factors": {
            "similarity": round(similarity, 4),
            "peak_clarity": round(prominence, 4),
            "temporal_consistency": round(consistency, 4) if consistency is not None else None,
            "model_version": CONFIDENCE_MODEL_VERSION,
            "reasons": reasons,
        },
    })
    return result


def episode_review_status(op: dict | None, ed: dict | None) -> str:
    """Aggregate segment states without making medium-confidence results blocking."""
    segments = [item for item in (op, ed) if item]
    if not segments:
        return "NOT_REQUIRED"
    unverified = [item for item in segments if not item.get("manual_verified")]
    if any(item.get("confidence_band") in {"LOW", "UNASSESSED", None} for item in unverified):
        return "PENDING"
    if any(item.get("confidence_band") == "MEDIUM" for item in unverified):
        return "REVIEW_RECOMMENDED"
    if any(item.get("manual_verified") for item in segments):
        return "MANUALLY_REVIEWED"
    return "AUTO_APPROVED"
