import copy
import json
import mlflow
import pytest
from investiq.lab.data import synthetic, split
from investiq.lab.models import AgentResult, OpenAICompatibleBackend, SimulatedAgentBackend
from investiq.lab.runner import Config, run_experiment


def config(cutoffs, **kwargs):
    return Config(**cutoffs, bootstrap_samples=100, monte_carlo_draws=8, **kwargs)


class Spy(SimulatedAgentBackend):
    def research(self, role, row, tool_signal):
        assert set(row) == {'entity_id','origin','signal','evidence','scenario_shock'}
        assert not {'future_return','label_available_at','verification_references','scenario_truth'} & set(row)
        assert all('secret_future' not in e for e in row['evidence'])
        return super().research(role, row, tool_signal)


def test_run_mlflow_reports_and_no_test_label_selection(tmp_path):
    dataset, cutoffs = synthetic(2, 30)
    for r in dataset['rows']: r['evidence'][0]['secret_future'] = 'never provide to agent'
    cfg = config(cutoffs, seed=2)
    first = run_experiment(dataset, cfg, tmp_path/'first', backend=Spy(2))
    changed = copy.deepcopy(dataset)
    parts, _ = split(changed, **cutoffs)
    for r in parts['test']: r['future_return'] = -r['future_return']
    second = run_experiment(changed, cfg, tmp_path/'second', backend=Spy(2))
    for arm in first['metrics']:
        assert [r['probability'] for r in first['test_records'][arm]] == [r['probability'] for r in second['test_records'][arm]]
    assert first['metrics']['E0']['brier'] != second['metrics']['E0']['brier']
    assert first['backend'] == 'synthetic-heuristic-not-an-llm'
    assert all(r['original_agents'] for r in first['test_records']['E3'])
    assert len(first['comparisons_vs_E0']) == 4
    assert (tmp_path/'first'/'results.tex').exists() and (tmp_path/'first'/'results.csv').exists()
    runid = (tmp_path/'first'/'mlflow_run_id.txt').read_text().strip()
    client = mlflow.tracking.MlflowClient(tracking_uri=(tmp_path/'first'/'mlruns').resolve().as_uri())
    run = client.get_run(runid)
    assert run.info.status == 'FINISHED' and run.data.params['dataset_version'] == dataset['dataset_version']
    assert 'E4.brier' in run.data.metrics and 'E4.vs_E0.p_holm' in run.data.metrics
    assert {'report.json','report.md','results.csv','results.tex'} <= {a.path for a in client.list_artifacts(runid)}


def test_e4_ablation_keeps_dissent_archive(tmp_path):
    dataset, cutoffs = synthetic(1, 30)
    report = run_experiment(dataset, config(cutoffs, ablation='hide_dissent'), tmp_path/'ablation')
    assert report['configuration']['ablation'] == 'hide_dissent'
    assert all(r['original_agents'] and not r['display_dissent'] for r in report['test_records']['E4'])


def test_live_backend_fails_closed_and_scopes_tools(monkeypatch):
    monkeypatch.delenv('INVESTIQ_LLM_ENDPOINT', raising=False)
    with pytest.raises(ValueError): OpenAICompatibleBackend('model')
    monkeypatch.setenv('INVESTIQ_LLM_ENDPOINT','https://api.example.test/v1/chat/completions')
    monkeypatch.setenv('INVESTIQ_LLM_API_KEY','secret')
    monkeypatch.setenv('INVESTIQ_LLM_ALLOWED_HOSTS','api.example.test')
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'choices':[{'message':{'content':json.dumps({'probability':.5,'expected_return':.01,'scenario_effect':.002,'claims':[]})}}], 'usage':{'prompt_tokens':1000,'completion_tokens':500}}
    def post(url, **kwargs):
        captured.update(kwargs); return Response()
    monkeypatch.setattr('investiq.lab.models.requests.post', post)
    backend = OpenAICompatibleBackend('model-v1', 2, 4)
    row = {'origin':'2020-01-01T00:00:00Z','entity_id':'x','evidence':[], 'scenario_shock':.01}
    result = backend.research('single', row, .02)
    assert result.provider_cost_usd == pytest.approx(.004)
    assert captured['allow_redirects'] is False
    assert 'as_of_financial_tool_results' in captured['json']['messages'][1]['content']


def test_failed_inference_is_logged_without_exception_details(tmp_path):
    class Failing(SimulatedAgentBackend):
        def research(self, role, row, tool_signal):
            raise RuntimeError('secret provider token')
    dataset, cutoffs = synthetic(1, 30)
    out = tmp_path/'failed'
    with pytest.raises(RuntimeError): run_experiment(dataset, config(cutoffs), out, Failing(1))
    failure = json.loads((out/'failure.json').read_text())
    assert failure['status'] == 'FAILED' and failure['failed_arm'] == 'E1'
    assert 'secret provider token' not in (out/'failure.json').read_text()
    client = mlflow.tracking.MlflowClient(tracking_uri=(out/'mlruns').resolve().as_uri())
    experiment = client.get_experiment_by_name('investiq-research-lab')
    runs = client.search_runs([experiment.experiment_id])
    assert runs[0].info.status == 'FAILED'


def test_no_calibration_ablation_and_output_overwrite_guard(tmp_path):
    dataset, cutoffs = synthetic(8, 30)
    out = tmp_path/'no-cal'
    report = run_experiment(dataset, config(cutoffs, ablation='no_calibration'), out)
    assert not report['computational_cost']['E4']['calibration_fitted']
    assert all(r['probability'] == pytest.approx(r['raw_probability']) for r in report['test_records']['E4'])
    with pytest.raises(ValueError): run_experiment(dataset, config(cutoffs), out)


def test_selection_stage_never_invokes_test_predictions(tmp_path):
    dataset, cutoffs = synthetic(9, 30)
    test_keys = {(r['origin'], r['entity_id']) for r in split(dataset, **cutoffs)[0]['test']}
    class SelectionSpy(SimulatedAgentBackend):
        def research(self, role, row, tool_signal):
            assert (row['origin'], row['entity_id']) not in test_keys
            return super().research(role, row, tool_signal)
    report = run_experiment(dataset, config(cutoffs, stage='selection'), tmp_path/'selection', SelectionSpy(9))
    assert report['stage'] == 'selection' and report['test_records'] == {}
    assert report['selection_records']['E1'] and report['computational_cost']['E1']['test_reports'] == 0
    assert not report['computational_cost']['E1']['calibration_fitted']
