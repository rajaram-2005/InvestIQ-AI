"""Chronological five-arm evaluation; no test-driven selection."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import platform
import time
import tracemalloc
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon

from investiq.prototypes import eccr
from .data import content_hash, split
from .metrics import claim_checks, evaluate, holm, paired_bootstrap
from .models import DeterministicFinancialBaseline, OpenAICompatibleBackend, SimulatedAgentBackend, ValidationCalibrator

ARMS = ('E0', 'E1', 'E2', 'E3', 'E4')
ABLATIONS = ('none', 'no_state_update', 'no_scenario_simulation', 'no_calibration', 'hide_dissent')


@dataclass(frozen=True)
class Config:
    train_end: str
    validation_end: str
    test_end: str
    seed: int = 42
    model_version: str = 'synthetic-heuristic-v1-not-an-llm'
    backend: str = 'simulated'
    stage: str = 'final'
    transaction_cost_bps: float = 10.0
    bootstrap_samples: int = 1000
    bootstrap_block_length: int = 5
    monte_carlo_draws: int = 128
    ablation: str = 'none'
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None

    def __post_init__(self):
        if self.backend not in ('simulated', 'live') or self.stage not in ('selection', 'final') or not self.model_version.strip() or self.ablation not in ABLATIONS:
            raise ValueError('invalid backend, model or ablation')
        if not 0 <= self.seed < 2**32 or not 100 <= self.bootstrap_samples <= 100000 or not 1 <= self.bootstrap_block_length <= 10000:
            raise ValueError('invalid seed/bootstrap settings')
        if not 1 <= self.monte_carlo_draws <= 10000 or not math.isfinite(self.transaction_cost_bps) or not 0 <= self.transaction_cost_bps <= 10000:
            raise ValueError('invalid simulation or transaction costs')
        if self.backend == 'simulated' and self.model_version != 'synthetic-heuristic-v1-not-an-llm':
            raise ValueError('simulated backend must use the explicitly labeled synthetic model version')


def _safe_view(row: dict) -> dict:
    # Future outcomes and independent verification references never enter any agent.
    allowed = ('id', 'source', 'metric', 'value', 'unit', 'published_at', 'available_at', 'retrieved_at', 'version_identifier', 'validation_status')
    return {**{k: row[k] for k in ('entity_id', 'origin', 'signal', 'scenario_shock')},
            'evidence': [{k: e[k] for k in allowed} for e in row['evidence']]}


def _scenario(row: dict, config: Config) -> dict:
    """Hypothetical fixed-coefficient SCM; not an identified causal effect."""
    evidence = row['evidence'][0]
    seed = int.from_bytes(hashlib.sha256(f"{config.seed}|{row['entity_id']}|{row['origin']}".encode()).digest()[:4], 'big')
    nodes = (eccr.Node('shock', 'fraction', 0., 0., assumption_evidence=('config-assumption',), allowed_range=(-1., 1.)),
             eccr.Node('outcome', 'fraction', 0., .01, {'shock': .3}, ('config-assumption',)))
    case = eccr.Scenario(row['origin'], nodes, (eccr.Evidence('config-assumption', 'experiment-parameter-not-causal-proof', evidence['available_at']),),
                         (), {'shock': eccr.Intervention(float(row['scenario_shock']), 'fraction')}, True, True)
    result = eccr.simulate(case, eccr.Config(draws=config.monte_carlo_draws, seed=seed))
    if result['status'] != 'OK':
        raise ValueError(f"scenario failed: {result['errors']}")
    distribution = result['distributions']['outcome']
    return {'mean_paired_delta': distribution['mean_paired_delta'], 'quantile_05_95': distribution['quantile_05_95'],
            'seed': seed, 'draws': config.monte_carlo_draws, 'label': 'hypothetical_unidentified_model'}


def _predict(arm: str, row: dict, baseline: DeterministicFinancialBaseline, backend, config: Config,
             state: dict) -> dict:
    raw = _safe_view(row)
    if arm == 'E0':
        p, expected = baseline.predict(raw)
        item = raw['evidence'][0]
        claims = [{'evidence_id': item['id'], 'metric': item['metric'], 'unit': item['unit'], 'value': item['value']}]
        return {'raw_probability': p, 'expected_return': expected, 'scenario_effect': 0.0, 'claims': claims,
                'provider_cost_usd': 0.0, 'input_tokens': 0, 'output_tokens': 0, 'original_agents': [], 'divergence_bits': None, 'scenario': None}
    signal = raw['signal']
    if arm == 'E4':
        old = state.get(raw['entity_id'], signal)
        state[raw['entity_id']] = .7 * old + .3 * signal
        if config.ablation != 'no_state_update':
            signal = state[raw['entity_id']]
    roles = ['single'] if arm == 'E1' else ['fundamental', 'risk', 'skeptic']
    agents = [backend.research(role, raw, signal) for role in roles]
    probs = [a.probability for a in agents]
    # Consensus and dissent differ in how the report presents evidence; equal mean is intentionally matched.
    p = float(np.mean(probs)); expected = float(np.mean([a.expected_return for a in agents]))
    scenario = None
    effect = float(np.mean([a.scenario_effect for a in agents]))
    if arm == 'E4' and config.ablation != 'no_scenario_simulation':
        scenario = _scenario(raw, config); effect = scenario['mean_paired_delta']
    distances = [float(jensenshannon([a,1-a],[b,1-b],base=2)**2) for i,a in enumerate(probs) for b in probs[i+1:]]
    costs = [a.provider_cost_usd for a in agents]
    return {'raw_probability': p, 'expected_return': expected, 'scenario_effect': effect,
            'claims': [claim for a in agents for claim in a.claims],
            'provider_cost_usd': float(sum(costs)) if all(c is not None for c in costs) else None,
            'input_tokens': sum(a.input_tokens for a in agents) if all(a.input_tokens is not None for a in agents) else None,
            'output_tokens': sum(a.output_tokens for a in agents) if all(a.output_tokens is not None for a in agents) else None,
            'original_agents': [asdict(a) for a in agents] if arm in ('E3','E4') else [],
            'display_dissent': arm in ('E3','E4') and not (arm == 'E4' and config.ablation == 'hide_dissent'),
            'divergence_bits': float(np.mean(distances)) if distances else None, 'scenario': scenario}


def _run_arm(arm: str, parts: dict, baseline, backend, config: Config) -> tuple[list[dict], dict]:
    state = {}
    if arm == 'E4':
        for row in parts['train']:
            state[row['entity_id']] = .7 * state.get(row['entity_id'], row['signal']) + .3 * row['signal']
    started = time.perf_counter(); tracemalloc.start()
    validation = [_predict(arm, row, baseline, backend, config, state) for row in parts['validation']]
    calibrator = ValidationCalibrator(enabled=config.stage == 'final' and not (arm == 'E4' and config.ablation == 'no_calibration'))
    if config.stage == 'final':
        calibrator.fit([r['raw_probability'] for r in validation], [int(r['future_return'] > 0) for r in parts['validation']])
        evaluated = [(_predict(arm, row, baseline, backend, config, state), row) for row in parts['test']]
    else:
        # Selection reports use uncalibrated validation outputs; no test predictions are generated.
        evaluated = list(zip(validation, parts['validation']))
    records = []
    for prediction, row in evaluated:
        checks = claim_checks(prediction['claims'], row['evidence'], row['verification_references'])
        records.append({'entity_id': row['entity_id'], 'origin': row['origin'], 'outcome': row['future_return'],
                        'label_available_at': row['label_available_at'], 'label_retrieved_at': row['label_retrieved_at'],
                        'delisted_after_origin': bool(row.get('delisted_after_origin', False)),
                        'evidence_versions': [{'id': e['id'], 'version_identifier': e['version_identifier'], 'source': e['source']} for e in row['evidence']],
                        'scenario_truth': row.get('scenario_truth'), 'probability': calibrator.predict(prediction['raw_probability']),
                        'claim_checks': checks, **prediction})
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    validation_costs = [r['provider_cost_usd'] for r in validation]
    return records, {'elapsed_seconds_validation_and_test': elapsed, 'python_peak_bytes': peak,
                     'validation_reports': len(validation), 'test_reports': len(records) if config.stage == 'final' else 0,
                     'calibration_fitted': calibrator.model is not None,
                     'validation_provider_cost_usd': float(sum(validation_costs)) if all(c is not None for c in validation_costs) else None,
                     'validation_input_tokens': sum(r['input_tokens'] for r in validation) if all(r['input_tokens'] is not None for r in validation) else None,
                     'validation_output_tokens': sum(r['output_tokens'] for r in validation) if all(r['output_tokens'] is not None for r in validation) else None}


def run_experiment(dataset: dict, config: Config, output_dir: Path, backend=None) -> dict:
    """Run a fixed protocol. Model selection/tuning is never performed on the test split."""
    started = datetime.now(timezone.utc).isoformat()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('output directory must be empty to avoid overwriting prior results')
    parts, exclusions = split(dataset, config.train_end, config.validation_end, config.test_end)
    if config.backend == 'simulated' and dataset['dataset_kind'] != 'synthetic':
        raise ValueError('simulated agents are limited to synthetic datasets')
    backend = backend or (SimulatedAgentBackend(config.seed) if config.backend == 'simulated'
        else OpenAICompatibleBackend(config.model_version, config.input_usd_per_million, config.output_usd_per_million))
    if config.backend == 'live' and isinstance(backend, SimulatedAgentBackend):
        raise ValueError('simulated backend cannot masquerade as live')
    fit_start = time.perf_counter()
    baseline = DeterministicFinancialBaseline().fit(parts['train'])
    baseline_fit_seconds = time.perf_counter() - fit_start
    records, metrics, costs = {}, {}, {}
    for arm in ARMS:
        try:
            records[arm], costs[arm] = _run_arm(arm, parts, baseline, backend, config)
            metrics[arm] = evaluate(records[arm], config.transaction_cost_bps)
        except Exception as exc:
            if tracemalloc.is_tracing(): tracemalloc.stop()
            output_dir.mkdir(parents=True, exist_ok=True)
            failure = {'status': 'FAILED', 'timestamp_utc': started, 'dataset_version': dataset['dataset_version'],
                       'dataset_sha256': content_hash(dataset), 'model_version': config.model_version, 'seed': config.seed,
                       'configuration': asdict(config), 'failed_arm': arm, 'error_type': type(exc).__name__,
                       'limitations': ['Failed run; no comparative conclusion.', 'Error messages suppressed to avoid exposing credentials or data.']}
            (output_dir / 'failure.json').write_text(json.dumps(failure, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            mlflow.set_tracking_uri((output_dir / 'mlruns').resolve().as_uri())
            mlflow.set_experiment('investiq-research-lab')
            with mlflow.start_run(run_name='failed-' + dataset['dataset_version']):
                mlflow.log_params({'dataset_version': dataset['dataset_version'], 'model_version': config.model_version,
                                   'seed': config.seed, 'failed_arm': arm, 'status': 'FAILED'})
                mlflow.log_artifact(str(output_dir / 'failure.json'))
                mlflow.end_run(status='FAILED')
            raise
    comparisons = {arm: paired_bootstrap(records[arm], records['E0'], config.seed, config.bootstrap_samples, config.bootstrap_block_length) for arm in ARMS[1:]}
    adjusted = holm({k: v['p_two_sided'] for k, v in comparisons.items() if v.get('status') == 'exploratory'})
    for key, pvalue in adjusted.items(): comparisons[key]['p_holm'] = pvalue
    hashes = {'lab/' + p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    hashes['prototypes/eccr.py'] = hashlib.sha256(Path(eccr.__file__).read_bytes()).hexdigest()
    lock = Path(__file__).resolve().parents[2] / 'requirements-lock.txt'
    hashes['requirements-lock.txt'] = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None
    limitations = ['Exploratory; no result establishes originality or financial performance.',
        'E4 is a limited integration prototype, not the complete production architecture.',
        'Scenario coefficient is assumed, not identified; synthetic scenario ground truth is not real causal validation.',
        'Historical LLM model weights may contain future information even with as-of tool inputs.',
        'Membership/delisting and source-license assertions require independent audit.',
        'Evidence matching is a numeric rule, not semantic fact verification.',
        'Reported provider cost excludes compute, staff, infrastructure and unknown token pricing.',
        'Portfolio is illustrative, uses simplified daily turnover/costs, and is not executable investment advice.',
        'Bootstrap p-values and intervals are exploratory and sensitive to block length and few independent dates.']
    if dataset['dataset_kind'] == 'synthetic': limitations.insert(0, 'Synthetic scenario truth uses the same 0.3 slope assumed by E4: scenario error is a wiring check, not a blinded performance comparison.')
    if config.backend == 'simulated': limitations.insert(0, 'E1–E4 use heuristic test doubles, NOT actual LLMs or agents.')
    report = {'record_version': 1, 'timestamp_utc': started, 'dataset_version': dataset['dataset_version'],
        'dataset_kind': dataset['dataset_kind'], 'dataset_sha256': content_hash(dataset), 'model_version': config.model_version,
        'backend': getattr(backend, 'name', type(backend).__name__), 'stage': config.stage, 'seed': config.seed, 'configuration': asdict(config),
        'execution_environment': {'python': platform.python_version(), 'mlflow': mlflow.__version__, 'numpy': np.__version__, 'pandas': pd.__version__},
        'code_hashes': hashes, 'split_sizes': {k: len(v) for k,v in parts.items()}, 'split_exclusions': exclusions,
        'metrics': metrics, 'computational_cost': costs, 'baseline_fit_seconds': baseline_fit_seconds, 'comparisons_vs_E0': comparisons,
        'negative_results': [f"{arm} has worse {metric} than E0 on the {config.stage} set (descriptive)."
            for arm in ARMS[1:] for metric in ('mae','rmse','brier','log_loss','expected_calibration_error','maximum_drawdown',
              'unsupported_claim_rate','scenario_error','risk_constraint_violations','cost_per_research_report_usd')
            if metrics[arm][metric] is not None and metrics['E0'][metric] is not None and metrics[arm][metric] > metrics['E0'][metric]] +
            [f"{arm} has lower evidence coverage than E0 on the {config.stage} set (descriptive)." for arm in ARMS[1:]
             if metrics[arm]['evidence_coverage'] < metrics['E0']['evidence_coverage']],
        'limitations': limitations, 'test_records': records if config.stage == 'final' else {},
        'selection_records': records if config.stage == 'selection' else {}}
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'report.json'
    path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    columns = ['n','mae','rmse','brier','log_loss','expected_calibration_error','maximum_drawdown','evidence_coverage',
               'unsupported_claim_rate','scenario_error','scenario_scored_n','risk_constraint_violations','cost_per_research_report_usd']
    frame = pd.DataFrame.from_dict(metrics, orient='index')[columns]
    frame.index.name = 'System'
    frame.to_csv(output_dir / 'results.csv', float_format='%.6f')
    (output_dir / 'results.tex').write_text(frame.to_latex(float_format=lambda x: f'{x:.4f}', na_rep='--', caption=f'Exploratory {config.stage} {dataset["dataset_kind"]} metrics ({report["backend"]}); lower is better for losses and drawdown; higher is better for evidence coverage.', label='tab:investiq-results') + '\n', encoding='utf-8')
    forecast_columns = ['n','mae','rmse','brier','log_loss','expected_calibration_error']
    evidence_columns = ['maximum_drawdown','evidence_coverage','unsupported_claim_rate','scenario_error',
                        'scenario_scored_n','risk_constraint_violations','cost_per_research_report_usd']
    (output_dir / 'forecast_metrics.tex').write_text(frame[forecast_columns].to_latex(
        float_format=lambda x: f'{x:.4f}', na_rep='--',
        caption=f'Exploratory {config.stage} forecast metrics: {dataset["dataset_kind"]}, {report["backend"]}.',
        label='tab:investiq-forecast') + '\n', encoding='utf-8')
    (output_dir / 'evidence_risk_metrics.tex').write_text(frame[evidence_columns].to_latex(
        float_format=lambda x: f'{x:.4f}', na_rep='--',
        caption=f'Exploratory {config.stage} evidence, scenario and risk metrics: {dataset["dataset_kind"]}, {report["backend"]}.',
        label='tab:investiq-evidence-risk') + '\n', encoding='utf-8')
    comparison_rows = []
    for arm, value in comparisons.items():
        comparison_rows.append({'System': arm, 'paired_brier_difference': value.get('difference'),
                                'ci_lower': value.get('ci_95', [None, None])[0], 'ci_upper': value.get('ci_95', [None, None])[1],
                                'p_two_sided': value.get('p_two_sided'), 'p_holm': value.get('p_holm'),
                                'dates': value.get('dates'), 'common_rows': value.get('common_rows')})
    comparison_frame = pd.DataFrame(comparison_rows).set_index('System')
    comparison_frame.to_csv(output_dir / 'comparisons.csv', float_format='%.6f')
    (output_dir / 'comparisons.tex').write_text(comparison_frame.to_latex(float_format=lambda x: f'{x:.4f}', na_rep='--',
        caption=f'Exploratory paired date-level Brier differences versus E0 ({dataset["dataset_kind"]}, {report["backend"]}); percentile block-bootstrap confidence intervals and Holm-adjusted p-values.',
        label='tab:investiq-comparisons') + '\n', encoding='utf-8')
    table = frame.to_string(float_format=lambda x: f'{x:.4f}', na_rep='--')
    comparison_table = comparison_frame.to_string(float_format=lambda x: f'{x:.4f}', na_rep='--')
    (output_dir / 'report.md').write_text('# InvestIQ exploratory experiment\n\n' + f"Dataset: `{dataset['dataset_version']}`; stage: `{config.stage}`; backend: `{report['backend']}`; seed: {config.seed}.\n\n" +
        '**Not a claim of real-world performance.** See report.json for all configuration, exclusions, uncertainty and per-record results.\n\n```text\n' + table + '\n```\n\n## Paired Brier comparison versus E0\n```text\n' + comparison_table + '\n```\n\n## Negative results\n' +
        ('\n'.join('- ' + x for x in report['negative_results']) if report['negative_results'] else '- No descriptive Brier underperformance versus E0 in this run; this does not establish superiority.') +
        '\n\n## Limitations\n' + '\n'.join('- ' + x for x in limitations) + '\n', encoding='utf-8')
    # Local file tracking by default, never an implicit remote upload.
    mlflow.set_tracking_uri((output_dir / 'mlruns').resolve().as_uri())
    mlflow.set_experiment('investiq-research-lab')
    with mlflow.start_run(run_name=f'{dataset["dataset_version"]}-{config.ablation}') as active:
        mlflow.log_params({'dataset_version': dataset['dataset_version'], 'dataset_sha256': report['dataset_sha256'],
                           'model_version': config.model_version, 'seed': config.seed, 'backend': report['backend'],
                           'ablation': config.ablation, 'stage': config.stage, 'execution_timestamp_utc': started})
        for arm, values in metrics.items():
            for key, value in values.items():
                if isinstance(value, (int, float)) and math.isfinite(value): mlflow.log_metric(f'{arm}.{key}', value)
        for arm, values in costs.items():
            for key, value in values.items():
                if isinstance(value, (int, float)) and math.isfinite(value): mlflow.log_metric(f'{arm}.{key}', value)
        mlflow.log_metric('baseline_fit_seconds', baseline_fit_seconds)
        for arm, comparison in comparisons.items():
            for key in ('difference', 'p_two_sided', 'p_holm'):
                if key in comparison: mlflow.log_metric(f'{arm}.vs_E0.{key}', comparison[key])
        for name in ('report.json', 'report.md', 'results.csv', 'results.tex', 'forecast_metrics.tex', 'evidence_risk_metrics.tex', 'comparisons.csv', 'comparisons.tex'):
            mlflow.log_artifact(str(output_dir / name))
        run_id = active.info.run_id
    # MLflow run_id is recorded separately to avoid mutating the logged report artifact.
    (output_dir / 'mlflow_run_id.txt').write_text(run_id + '\n', encoding='utf-8')
    return report
