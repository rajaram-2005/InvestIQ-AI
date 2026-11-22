from dataclasses import replace
import pytest
from investiq.prototypes import dpro

T = "2020-01-01T00:00:00Z"
def evidence():
    return [dpro.Evidence("a", "source1", "revenue_growth", "Q1", 0.1, "fraction", T),
            dpro.Evidence("b", "source2", "revenue_growth", "Q1", 0.2, "fraction", T),
            dpro.Evidence("c", "source1", "debt_ratio", "Q1", 0.3, "fraction", T)]


def test_independence_preservation_contradictions_divergence():
    items = evidence()
    original = dpro.generate_hypotheses(items, T)
    assert len(original) == 3 and len({h.probability for h in original}) > 1
    report = dpro.report(original, items, [], T)
    assert len(report['original_hypotheses']) == 3
    assert report['contradictions'][0]['left'] == 'a'
    assert report['mean_pairwise_js_divergence_bits'] > 0
    assert dpro.generate_hypotheses(items, T) == original
    hidden = dpro.report(original, items, [], T, dpro.Config(preserve_dissent=False))
    assert hidden['display_hypotheses'] == [] and len(hidden['original_hypotheses']) == 3
    assert len(original) == 3


def test_independent_claim_verification():
    items = evidence()
    claim = dpro.Claim('debt_ratio', 'Q1', 0.3, 'fraction', 'c')
    ref = dpro.Evidence('ref', 'independent-source', 'debt_ratio', 'Q1', 0.3, 'fraction', T)
    assert dpro.verify_claim(claim, items, [ref], T)['status'] == 'SUPPORTED_BY_REFERENCE'
    assert dpro.verify_claim(claim, items, [replace(ref, value=0.6)], T)['status'] == 'CONTRADICTED_BY_REFERENCE'
    assert dpro.verify_claim(claim, items, [replace(ref, source='source1')], T)['status'] == 'UNVERIFIABLE'
    assert dpro.verify_claim(replace(claim, value=0.4), items, [ref], T)['status'] == 'INVALID_CITATION'


def test_future_invalid_evidence_and_abstain():
    with pytest.raises(ValueError): dpro.generate_hypotheses([replace(evidence()[0], available_at="2021-01-01T00:00:00Z")], T)
    with pytest.raises(ValueError): dpro.generate_hypotheses([replace(evidence()[0], unit="USD")], T)
    assert dpro.generate_hypotheses([], T) == ()
    conflict = evidence()[:2]
    assert dpro.generate_hypotheses(conflict, T, dpro.Config(exclude_conflicting_metrics=True)) == ()
    assert dpro.report([], conflict, [], T)['status'] == 'ABSTAIN'


def test_benchmark_replay():
    a = dpro.benchmark(seed=8, cases=20)
    assert a == dpro.benchmark(seed=8, cases=20)
    assert 0 <= a['brier'] <= 1 and 0 <= a['conflict_detection_rate'] <= 1
