"""Predeclared financial, provenance and paired inferential metrics."""
from __future__ import annotations

import math
from collections import defaultdict
import numpy as np
from scipy import stats
from sklearn.metrics import mean_absolute_error, mean_squared_error, brier_score_loss, log_loss


def claim_checks(claims: list[dict], evidence: list[dict], references: list[dict], tolerance: float = 1e-8) -> dict:
    valid = supported = 0
    for claim in claims:
        matches = [e for e in evidence if e['id'] == claim['evidence_id'] and e['metric'] == claim['metric']
                   and e['unit'] == claim['unit'] and math.isclose(float(e['value']), float(claim['value']), rel_tol=0, abs_tol=tolerance)]
        if matches:
            valid += 1
            reference = [r for r in references if r['metric'] == claim['metric'] and r['unit'] == claim['unit'] and r['source'] != matches[0]['source']]
            if reference and all(math.isclose(float(r['value']), float(claim['value']), rel_tol=0, abs_tol=tolerance) for r in reference):
                supported += 1
    return {'claims': len(claims), 'valid_citations': valid, 'supported': supported,
            'unsupported': len(claims) - supported}


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    indices = np.minimum((np.clip(p, 0, 1) * bins).astype(int), bins - 1)
    return float(sum(np.mean(indices == i) * abs(np.mean(p[indices == i]) - np.mean(y[indices == i]))
                     for i in range(bins) if np.any(indices == i)))


def portfolio(records: list[dict], cost_bps: float, max_position: float = 0.25) -> dict:
    """Long-only equal weight per origin, one-period returns, constant-weight turnover approximation."""
    if not math.isfinite(cost_bps) or cost_bps < 0 or not 0 < max_position <= 1:
        raise ValueError('invalid portfolio settings')
    by_date = defaultdict(list)
    for r in records: by_date[r['origin']].append(r)
    if not by_date:
        raise ValueError('portfolio requires records')
    previous = {}; equity = peak = 1.0; max_drawdown = 0.0; violations = 0; turnover = 0.0
    dates = sorted(by_date)
    for i, date in enumerate(dates):
        rows = by_date[date]
        selected = [r for r in rows if r['probability'] >= 0.5]
        weight = min(max_position, 1 / len(selected)) if selected else 0.0
        positions = {r['entity_id']: weight for r in selected}
        changes = sum(abs(positions.get(k, 0.0) - previous.get(k, 0.0)) for k in set(positions) | set(previous))
        # End-of-study liquidation is charged on the final date.
        if i == len(dates) - 1:
            changes += sum(abs(v) for v in positions.values())
        turnover += changes
        gross = sum(positions.get(r['entity_id'], 0.0) * r['outcome'] for r in rows)
        net = gross - changes * cost_bps / 10000.0
        if net < -1:
            raise ValueError('portfolio net loss exceeds invested equity')
        equity *= 1 + net
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
        if sum(abs(v) for v in positions.values()) > 1 + 1e-12 or any(v > max_position + 1e-12 or v < 0 for v in positions.values()):
            violations += 1
        previous = positions
    return {'maximum_drawdown': max_drawdown, 'risk_constraint_violations': violations,
            'total_turnover': turnover, 'terminal_wealth': equity}


def evaluate(records: list[dict], cost_bps: float) -> dict:
    if not records:
        raise ValueError('no records to score')
    y = np.array([r['outcome'] for r in records], dtype=float)
    p = np.array([r['probability'] for r in records], dtype=float)
    prediction = np.array([r['expected_return'] for r in records], dtype=float)
    event = (y > 0).astype(int)
    clipped = np.clip(p, 1e-6, 1 - 1e-6)
    total_claims = sum(r['claim_checks']['claims'] for r in records)
    supported = sum(r['claim_checks']['supported'] for r in records)
    costs = [r['provider_cost_usd'] for r in records]
    return {'n': len(records), 'mae': float(mean_absolute_error(y, prediction)),
            'rmse': float(np.sqrt(mean_squared_error(y, prediction))),
            'brier': float(brier_score_loss(event, p)), 'log_loss': float(log_loss(event, clipped, labels=[0, 1])),
            'expected_calibration_error': expected_calibration_error(event, p),
            'evidence_coverage': float(np.mean([r['claim_checks']['valid_citations'] > 0 for r in records])),
            'unsupported_claim_rate': 1 - supported / total_claims if total_claims else None,
            'scenario_error': float(np.mean([abs(r['scenario_effect'] - r['scenario_truth']) for r in records if r['scenario_truth'] is not None])) if any(r['scenario_truth'] is not None for r in records) else None,
            'scenario_scored_n': sum(r['scenario_truth'] is not None for r in records),
            'cost_per_research_report_usd': float(sum(costs) / len(costs)) if all(c is not None for c in costs) else None,
            'total_claims': total_claims, **portfolio(records, cost_bps)}


def paired_bootstrap(a: list[dict], b: list[dict], seed: int, samples: int = 2000, block_length: int = 5) -> dict:
    """Paired Brier difference A-B; dates resampled in contiguous blocks, all entities together."""
    if samples < 100 or block_length < 1:
        raise ValueError('invalid bootstrap configuration')
    key = lambda r: (r['origin'], r['entity_id'])
    aa, bb = {key(r): r for r in a}, {key(r): r for r in b}
    common = sorted(set(aa) & set(bb))
    by_date = defaultdict(list)
    for k in common:
        if not math.isclose(aa[k]['outcome'], bb[k]['outcome'], abs_tol=1e-12):
            raise ValueError('paired outcomes differ')
        event = float(aa[k]['outcome'] > 0)
        by_date[k[0]].append((aa[k]['probability'] - event)**2 - (bb[k]['probability'] - event)**2)
    dates = sorted(by_date)
    if len(dates) < 2:
        return {'status': 'insufficient_dates', 'common_rows': len(common), 'dates': len(dates)}
    # Equal weight per date; all entities within a date remain together.
    differences = np.array([np.mean(by_date[d]) for d in dates])
    n = len(differences); length = min(block_length, n); rng = np.random.default_rng(seed)
    draws = np.empty(samples)
    for i in range(samples):
        starts = rng.integers(0, n - length + 1, size=math.ceil(n / length))
        indices = np.concatenate([np.arange(s, s + length) for s in starts])[:n]
        draws[i] = np.mean(differences[indices])
    observed = float(np.mean(differences))
    # Centered bootstrap approximates the two-sided null of zero mean difference.
    p_value = float((1 + np.sum(np.abs(draws - observed) >= abs(observed))) / (samples + 1))
    return {'status': 'exploratory', 'estimand': 'mean date-level paired Brier(A-B)', 'difference': observed,
            'ci_95': np.quantile(draws, [0.025, 0.975]).tolist(), 'p_two_sided': p_value,
            'common_rows': len(common), 'dates': n, 'block_length': length, 'bootstrap_samples': samples}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda pair: pair[1])
    result = {}; maximum = 0.0; n = len(ordered)
    for index, (name, p) in enumerate(ordered):
        maximum = max(maximum, min(1.0, p * (n - index)))
        result[name] = maximum
    return result
