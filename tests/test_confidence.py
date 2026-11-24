from __future__ import annotations

from core.confidence import assess_segment, episode_review_status


def _segment(similarity: float, prominence: float, **extra):
    return {
        "name": "OP1",
        "start": 90.0,
        "end": 180.0,
        "score": 320.0,
        "similarity_score": similarity,
        "peak_prominence": prominence,
        **extra,
    }


def test_confidence_index_is_bounded_and_uses_high_band():
    assessed = assess_segment(_segment(0.95, 0.90))

    assert assessed is not None
    assert assessed["confidence_score"] == 93
    assert assessed["confidence_band"] == "HIGH"
    assert 0 <= assessed["confidence_score"] <= 100
    assert assessed["confidence_factors"]["model_version"] == "heuristic-confidence-v1"


def test_confidence_index_distinguishes_medium_and_low_results():
    medium = assess_segment(_segment(0.82, 0.60))
    low = assess_segment(_segment(0.20, 0.10))

    assert medium["confidence_score"] == 75
    assert medium["confidence_band"] == "MEDIUM"
    assert low["confidence_score"] == 17
    assert low["confidence_band"] == "LOW"


def test_confidence_index_includes_template_margin_and_temporal_consistency():
    assessed = assess_segment(
        _segment(0.90, 0.80, template_margin=0.70),
        temporal_consistency=0.60,
    )

    assert assessed["confidence_score"] == 82
    assert assessed["confidence_band"] == "MEDIUM"
    factors = assessed["confidence_factors"]
    assert factors["template_margin"] == 0.7
    assert factors["temporal_consistency"] == 0.6


def test_anomalous_detection_is_capped_below_auto_approval():
    assessed = assess_segment(_segment(1.0, 1.0), anomalous=True)

    assert assessed["confidence_score"] == 69
    assert assessed["confidence_band"] == "LOW"


def test_missing_normalized_metrics_remain_unassessed():
    assessed = assess_segment({"name": "OP1", "start": 90, "end": 180, "score": 320})

    assert assessed["confidence_score"] is None
    assert assessed["confidence_band"] == "UNASSESSED"


def test_episode_review_status_separates_auto_recommended_and_blocking_review():
    high = assess_segment(_segment(0.95, 0.90))
    medium = assess_segment(_segment(0.82, 0.60))
    low = assess_segment(_segment(0.20, 0.10))

    assert episode_review_status(high, None) == "AUTO_APPROVED"
    assert episode_review_status(medium, None) == "REVIEW_RECOMMENDED"
    assert episode_review_status(low, None) == "PENDING"
    assert episode_review_status({**low, "manual_verified": True}, None) == "MANUALLY_REVIEWED"
    assert episode_review_status(None, None) == "NOT_REQUIRED"
