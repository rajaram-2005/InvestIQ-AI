from dataclasses import replace
import numpy as np
import pytest
from investiq.prototypes import eccr


def test_reproducible_and_structural_relationship():
    scenario = eccr.example_scenario()
    result = eccr.simulate(scenario, eccr.Config(draws=300, seed=2))
    assert result == eccr.simulate(scenario, eccr.Config(draws=300, seed=2))
    assert result["status"] == "OK" and result["evidence_ids"] == ["doc-1"]
    assert result["evidence"][0]["source"] == "synthetic:scenario-assumptions"
    assert result["intervention"]["income"]["unit"] == "USD/month"
    savings = np.array(result["distributions"]["savings"]["samples"])
    expense = np.array(result["distributions"]["expense"]["samples"])
    assert np.std(savings + expense) == pytest.approx(30, abs=4)
    assert abs(result["distributions"]["savings"]["mean_paired_delta"] - 500) < 20


def test_fail_closed_cycles_sources_approval_units_and_ranges():
    scenario = eccr.example_scenario()
    cyc = replace(scenario.nodes[0], parents={"savings": 1})
    assert eccr.simulate(replace(scenario, nodes=(cyc, *scenario.nodes[1:])))['status'] == 'ABSTAIN'
    assert eccr.simulate(replace(scenario, assumptions_approved=False))['status'] == 'ABSTAIN'
    assert eccr.simulate(replace(scenario, no_unmeasured_confounding_assumed=False))['status'] == 'ABSTAIN'
    assert eccr.simulate(replace(scenario, intervention={"income": eccr.Intervention(6000, "USD/month")}))['status'] == 'ABSTAIN'
    assert eccr.simulate(replace(scenario, intervention={"income": eccr.Intervention(3000, "EUR/month")}))['status'] == 'ABSTAIN'
    assert eccr.simulate(replace(scenario, observations=(replace(scenario.observations[0], unit="EUR"),)))['status'] == 'ABSTAIN'
    future = replace(scenario.evidence[0], available_at="2021-01-01T00:00:00Z")
    assert eccr.simulate(replace(scenario, evidence=(future,)))['status'] == 'ABSTAIN'
    assert eccr.simulate(replace(scenario, nodes=(replace(scenario.nodes[0], assumption_evidence=("missing",)), *scenario.nodes[1:])))['status'] == 'ABSTAIN'


def test_policy_ablation_never_claims_causal_validation():
    scenario = replace(eccr.example_scenario(), assumptions_approved=False, intervention={"income": eccr.Intervention(6000, "USD/month")})
    result = eccr.simulate(scenario, eccr.Config(draws=10, enforce_approval=False, enforce_support=False))
    assert result['status'] == 'OK' and result['interpretation'] == 'model_conditional_hypothetical'
    assert len(result['warnings']) >= 2
    result = eccr.benchmark(seed=1, draws=3000)
    assert result['absolute_error'] < 10
    assert result['baseline_absolute_error'] == pytest.approx(500)
