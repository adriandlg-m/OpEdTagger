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
        "np.linalg.norm(chroma_ep, axis=0)",
        "np.linalg.norm(chroma_tmpl, axis=0)",
        'scipy.signal.correlate(chroma_ep[i], chroma_tmpl[i], mode="valid")',
        "correlation /= chroma_ep.shape[0]",
        "match_index = np.argmax(correlation)",
        "max_score >= COSINE_SIMILARITY_THRESHOLD",
        "start_time < (episode_duration * 0.70)",
    )
    for contract in required_contract:
        assert contract in source, f"The tuned OP/ED detector contract changed: {contract}"
