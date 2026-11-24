from __future__ import annotations

import math
from typing import Any

CONFIDENCE_MODEL_VERSION = "split-confidence-v2"
HIGH_CONFIDENCE_THRESHOLD = 90
MEDIUM_CONFIDENCE_THRESHOLD = 70

# Peak width is measured at 95% of the best normalized correlation value.
# At/under 0.10 s the alignment is considered sharply localized; at/over
# 2.0 s it is considered broad/ambiguous. These are initial heuristic limits.
PEAK_WIDTH_EXCELLENT_SECONDS = 0.10
PEAK_WIDTH_POOR_SECONDS = 2.0


def _unit_interval(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


def _positive_seconds(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _weighted_score(factors: list[tuple[float | None, float]]) -> int | None:
    available = [(value, weight) for value, weight in factors if value is not None]
    total_weight = sum(weight for _value, weight in available)
    if total_weight <= 0:
        return None
    value = sum(value * weight for value, weight in available) / total_weight
    return int(round(max(0.0, min(1.0, value)) * 100))


def _band(score: int | None) -> str:
    if score is None:
        return "UNASSESSED"
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def assess_segment(segment: dict | None, *, temporal_consistency: float | None = None,
                   anomalous: bool = False) -> dict | None:
    """Assess match identity and temporal localization as separate heuristics.

    Neither score is a calibrated probability. Temporal precision estimates
    how narrowly the chroma alignment identifies the segment start. The end
    timestamp is still derived from the source template duration.
    """
    if segment is None:
        return None

    result = dict(segment)
    similarity = _unit_interval(result.get("similarity_score"))
    template_margin = _unit_interval(result.get("template_margin"))
    prominence = _unit_interval(result.get("peak_prominence"))
    consistency = _unit_interval(temporal_consistency)
    peak_width = _positive_seconds(result.get("peak_width_seconds"))

    # Match confidence: similarity to the candidate template and separation
    # from the next-best template. Missing alternatives do not count as zero.
    match_score = _weighted_score([
        (similarity, 0.75),
        (template_margin, 0.25),
    ])

    # Temporal precision: local width of the alignment peak and distinction
    # from other positions in the same episode. Without a measured peak width
    # there is not enough evidence to issue a temporal precision score.
    if peak_width is None:
        alignment_sharpness = None
        temporal_score = None
    else:
        span = PEAK_WIDTH_POOR_SECONDS - PEAK_WIDTH_EXCELLENT_SECONDS
        alignment_sharpness = max(
            0.0,
            min(1.0, (PEAK_WIDTH_POOR_SECONDS - peak_width) / span),
        )
        temporal_score = _weighted_score([
            (alignment_sharpness, 0.65),
            (prominence, 0.35),
        ])

    match_band = _band(match_score)
    temporal_band = _band(temporal_score)
    # Compatibility fields represent the weaker dimension, while the UI
    # presents the two independent values explicitly.
    aggregate_score = (
        min(match_score, temporal_score)
        if match_score is not None and temporal_score is not None
        else None
    )
    aggregate_band = _band(aggregate_score)

    reasons = []
    if similarity is not None:
        reasons.append(f"Similitud normalizada del template: {similarity:.3f}.")
    else:
        reasons.append("No hay similitud normalizada suficiente para evaluar la coincidencia.")
    if template_margin is not None:
        reasons.append(f"Separación frente a otros templates: {template_margin:.3f}.")
    else:
        reasons.append("No hay otro template candidato comparable para medir ambigüedad.")
    if peak_width is not None:
        reasons.append(
            f"Ancho del pico de alineación al 95%: {peak_width:.3f} s "
            "(menor anchura indica una posición de inicio más definida)."
        )
    else:
        reasons.append("No se pudo medir la anchura del pico de alineación temporal.")
    if prominence is not None:
        reasons.append(f"Distinción frente a otras posiciones del episodio: {prominence:.3f}.")
    if consistency is not None:
        reasons.append(
            f"Consistencia temporal entre episodios del mismo template: {consistency:.3f}."
        )
    else:
        reasons.append(
            "Aún no hay suficientes episodios comparables para medir consistencia temporal."
        )
    if anomalous:
        reasons.append(
            "Advertencia: el inicio se desvía del patrón de otros episodios que usan el mismo template; "
            "esto no reduce por sí solo las puntuaciones."
        )
    reasons.append(
        "El final se calcula sumando la duración del template al inicio detectado; "
        "no se verifica como un segundo límite independiente."
    )

    result.update({
        "match_confidence_score": match_score,
        "match_confidence_band": match_band,
        "temporal_precision_score": temporal_score,
        "temporal_precision_band": temporal_band,
        "confidence_score": aggregate_score,
        "confidence_band": aggregate_band,
        "temporal_outlier": bool(anomalous),
        "confidence_factors": {
            "similarity": round(similarity, 4) if similarity is not None else None,
            "template_margin": round(template_margin, 4) if template_margin is not None else None,
            "peak_clarity": round(prominence, 4) if prominence is not None else None,
            "peak_width_seconds": round(peak_width, 4) if peak_width is not None else None,
            "alignment_sharpness": round(alignment_sharpness, 4) if alignment_sharpness is not None else None,
            "temporal_consistency": round(consistency, 4) if consistency is not None else None,
            "model_version": CONFIDENCE_MODEL_VERSION,
            "reasons": reasons,
        },
    })
    return result


def episode_review_status(op: dict | None, ed: dict | None) -> str:
    """Require attention if either confidence dimension needs it.

    Cross-episode outliers recommend a review but do not artificially lower
    either numeric score.
    """
    segments = [item for item in (op, ed) if item]
    if not segments:
        return "NOT_REQUIRED"

    unverified = [item for item in segments if not item.get("manual_verified")]
    if any(
        item.get("match_confidence_band") in {"LOW", "UNASSESSED", None}
        or item.get("temporal_precision_band") in {"LOW", "UNASSESSED", None}
        for item in unverified
    ):
        return "PENDING"
    if any(
        item.get("match_confidence_band") == "MEDIUM"
        or item.get("temporal_precision_band") == "MEDIUM"
        or item.get("temporal_outlier")
        for item in unverified
    ):
        return "REVIEW_RECOMMENDED"
    if any(item.get("manual_verified") for item in segments):
        return "MANUALLY_REVIEWED"
    return "AUTO_APPROVED"
