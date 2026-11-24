from __future__ import annotations

import inspect

from core import analyzer, fingerprints


def test_op_ed_matcher_contract_remains_unchanged():
    """Guard the carefully tuned matcher while infrastructure around it evolves."""
    assert analyzer.COSINE_SIMILARITY_THRESHOLD == 0.68
    assert fingerprints.ANALYZER_VERSION == "chroma-template-v1"

    source = inspect.getsource(analyzer.evaluate_episode_templates)
    required_contract = (
        "sr = 22050",
        "hop_length = 512",
        "librosa.feature.chroma_stft",
        "np.linalg.norm(chroma_ep, axis=0, keepdims=True)",
        "np.linalg.norm(chroma_tmpl, axis=0, keepdims=True)",
        'scipy.signal.correlate(chroma_ep[i], chroma_tmpl[i], mode="valid")',
        "correlation /= chroma_ep.shape[0]",
        "match_index = np.argmax(correlation)",
        "max_score >= COSINE_SIMILARITY_THRESHOLD",
        "start_time < (episode_duration * 0.70)",
    )
    for contract in required_contract:
        assert contract in source, f"The tuned OP/ED detector contract changed: {contract}"


def test_alignment_peak_width_is_measured_without_changing_match_selection():
    import numpy as np

    values = np.array([0.1, 0.4, 0.90, 1.0, 0.97, 0.7, 0.2], dtype=float)
    width = analyzer._peak_width_seconds(values, 3, sr=100, hop_length=10)
    assert width == 0.2

    # A peak clipped by the search boundary is censored, not reported as precise.
    assert analyzer._peak_width_seconds(values, 0, sr=100, hop_length=10) is None
