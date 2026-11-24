from __future__ import annotations

from core.confidence import assess_segment, episode_review_status


def _segment(similarity: float, prominence: float, *, peak_width: float = 0.1, **extra):
    return {
        "name": "OP1",
        "start": 90.0,
        "end": 180.0,
        "score": 320.0,
        "similarity_score": similarity,
        "peak_prominence": prominence,
        "peak_width_seconds": peak_width,
        **extra,
    }


def test_split_confidence_scores_are_bounded_and_independent():
    assessed = assess_segment(_segment(0.95, 0.90, template_margin=0.90))

    assert assessed is not None
    assert assessed["match_confidence_score"] == 94
    assert assessed["match_confidence_band"] == "HIGH"
    assert assessed["temporal_precision_score"] == 97
    assert assessed["temporal_precision_band"] == "HIGH"
    assert assessed["confidence_score"] == 94
    assert assessed["confidence_band"] == "HIGH"
    assert assessed["confidence_factors"]["model_version"] == "split-confidence-v2"


def test_good_timestamps_can_have_high_temporal_precision_with_low_match_confidence():
    assessed = assess_segment(
        _segment(0.72, 0.95, peak_width=0.12, template_margin=0.10)
    )

    assert assessed["match_confidence_band"] == "LOW"
    assert assessed["temporal_precision_band"] == "HIGH"
    assert episode_review_status(assessed, None) == "PENDING"


def test_good_match_can_have_low_temporal_precision():
    assessed = assess_segment(
        _segment(0.98, 0.10, peak_width=2.4, template_margin=0.95)
    )

    assert assessed["match_confidence_band"] == "HIGH"
    assert assessed["temporal_precision_band"] == "LOW"
    assert episode_review_status(assessed, None) == "PENDING"


def test_medium_score_in_either_dimension_recommends_review():
    assessed = assess_segment(
        _segment(0.84, 0.78, peak_width=0.7, template_margin=0.80)
    )

    assert assessed["match_confidence_band"] == "MEDIUM"
    assert assessed["temporal_precision_band"] == "MEDIUM"
    assert episode_review_status(assessed, None) == "REVIEW_RECOMMENDED"


def test_missing_peak_width_keeps_temporal_precision_unassessed():
    assessed = assess_segment({
        "name": "OP1", "start": 90, "end": 180, "score": 320,
        "similarity_score": 0.95, "peak_prominence": 0.9,
    })

    assert assessed["match_confidence_band"] == "HIGH"
    assert assessed["temporal_precision_score"] is None
    assert assessed["temporal_precision_band"] == "UNASSESSED"
    assert episode_review_status(assessed, None) == "PENDING"


def test_anomaly_is_a_review_warning_not_a_numeric_score_cap():
    assessed = assess_segment(_segment(0.98, 0.95, template_margin=0.95), anomalous=True)

    assert assessed["match_confidence_score"] == 97
    assert assessed["temporal_precision_score"] == 98
    assert assessed["temporal_outlier"] is True
    assert episode_review_status(assessed, None) == "REVIEW_RECOMMENDED"


def test_temporal_consistency_is_contextual_and_does_not_reduce_scores():
    baseline = assess_segment(_segment(0.90, 0.90, template_margin=0.90))
    contextual = assess_segment(
        _segment(0.90, 0.90, template_margin=0.90),
        temporal_consistency=0.10,
    )

    assert contextual["match_confidence_score"] == baseline["match_confidence_score"]
    assert contextual["temporal_precision_score"] == baseline["temporal_precision_score"]
    assert contextual["confidence_factors"]["temporal_consistency"] == 0.1


def test_episode_review_status_aggregates_both_metrics_and_manual_verification():
    high = assess_segment(_segment(0.95, 0.95, template_margin=0.95))
    medium = assess_segment(_segment(0.84, 0.78, peak_width=0.7, template_margin=0.80))
    low_match = assess_segment(_segment(0.72, 0.95, peak_width=0.12, template_margin=0.10))

    assert episode_review_status(high, None) == "AUTO_APPROVED"
    assert episode_review_status(medium, None) == "REVIEW_RECOMMENDED"
    assert episode_review_status(low_match, None) == "PENDING"
    assert episode_review_status({**low_match, "manual_verified": True}, None) == "MANUALLY_REVIEWED"
    assert episode_review_status(None, None) == "NOT_REQUIRED"
