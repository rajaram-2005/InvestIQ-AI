from dataclasses import replace
import json
import subprocess
import sys

import pytest
from investiq.prototypes import dpro, eccr, tfsf
from investiq.prototypes.run import ABLATIONS, run


def test_prototype_pipeline_preserves_provenance_and_snapshots():
    observations = [tfsf.Observation('2020-01-01T00:00:00Z', {'income': 3100.0}, {'income': 100.0})]
    state = tfsf.fuse(observations)[0]
    scenario = eccr.example_scenario()
    scenario = replace(scenario, observations=(eccr.Observation('income', state.mean[0], 'USD/month', 'doc-1'),))
    result = eccr.simulate(scenario, eccr.Config(draws=50, seed=7))
    assert result['status'] == 'OK' and result['observations'][0]['evidence_id'] == 'doc-1'
    # Scenario is not conditioned on the state: provenance != calibration.
    assert result['distributions']['income']['mean'] == pytest.approx(3500)
    evidence = [dpro.Evidence('e1', 'financial-data', 'revenue_growth', 'Q1', 0.1, 'fraction', '2020-01-01T00:00:00Z')]
    ref = [replace(evidence[0], id='ref', source='independent-data')]
    hypotheses = dpro.generate_hypotheses(evidence, '2020-01-02T00:00:00Z')
    report = dpro.report(hypotheses, evidence, ref, '2020-01-02T00:00:00Z')
    assert len(report['original_hypotheses']) == 3
    assert all(x['verification']['status'] == 'SUPPORTED_BY_REFERENCE' for x in report['claim_checks'])


@pytest.mark.parametrize('algorithm,ablation', [(a,b) for a, options in ABLATIONS.items() for b in options])
def test_all_ablation_records_replay(algorithm, ablation):
    first = run(algorithm, ablation, 9, 20)
    second = run(algorithm, ablation, 9, 20)
    assert first == second
    assert first['ablation'] == ablation and first['source_sha256'] and first['config']
    json.dumps(first, allow_nan=False)


def test_cli_and_invalid_ablation(tmp_path):
    path = tmp_path / 'record.json'
    subprocess.run([sys.executable, '-m', 'investiq.prototypes.run', '--algorithm', 'eccr', '--seed', '1', '--size', '30', '--output', str(path)], check=True)
    assert json.loads(path.read_text())['result']['status'] == 'OK'
    with pytest.raises(ValueError): run('dpro', 'made-up', 1, 20)
