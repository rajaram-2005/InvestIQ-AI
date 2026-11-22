"""Deterministic, independent research heuristics with preserved disagreement."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon
from sklearn.metrics import brier_score_loss


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    metric: str
    period: str
    value: float
    unit: str
    available_at: str


@dataclass(frozen=True)
class Claim:
    metric: str
    period: str
    value: float
    unit: str
    evidence_id: str


@dataclass(frozen=True)
class Hypothesis:
    agent: str
    probability: float
    conclusion: str
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class Config:
    preserve_dissent: bool = True
    exclude_conflicting_metrics: bool = False
    tolerance: float = 1e-6

    def __post_init__(self):
        if not math.isfinite(self.tolerance) or self.tolerance < 0:
            raise ValueError("tolerance must be finite and non-negative")


def _validate(items: Sequence[Evidence], as_of: str) -> None:
    ts = pd.Timestamp(as_of)
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    ids = set()
    for item in items:
        available = pd.Timestamp(item.available_at)
        if (not item.id or item.id in ids or not item.source or not item.metric or not item.period or not item.unit
                or not math.isfinite(item.value) or pd.isna(available) or available.tzinfo is None or available > ts):
            raise ValueError("invalid, duplicate or future evidence")
        ids.add(item.id)


def contradictions(evidence: Sequence[Evidence], tolerance: float = 1e-6) -> list[dict]:
    """Flag incompatible same-metric/period/unit numeric observations, not truth."""
    result = []
    for i, left in enumerate(evidence):
        for right in evidence[i + 1:]:
            if ((left.metric, left.period, left.unit) == (right.metric, right.period, right.unit)
                    and not math.isclose(left.value, right.value, rel_tol=0.0, abs_tol=tolerance)):
                result.append({"left": left.id, "right": right.id, "metric": left.metric, "period": left.period,
                               "values": [left.value, right.value], "unit": left.unit})
    return result


def generate_hypotheses(evidence: Sequence[Evidence], as_of: str, config: Config = Config()) -> tuple[Hypothesis, ...]:
    """Three fixed heuristics independently read the same snapshot, not peers."""
    _validate(evidence, as_of)
    conflicts = contradictions(evidence, config.tolerance)
    excluded = {(item["metric"], item["period"]) for item in conflicts} if config.exclude_conflicting_metrics else set()
    chosen = {}
    # Deterministic latest-publication selection; conflicting records remain visible.
    for item in sorted(evidence, key=lambda e: (pd.Timestamp(e.available_at), e.id)):
        if (item.metric, item.period) not in excluded:
            chosen[(item.metric, item.period)] = item
    # Restrict to one explicitly shared period: avoid mixing unrelated dates.
    periods = {e.period for e in evidence}
    if not periods:
        return ()
    if len(periods) != 1:
        raise ValueError("prototype requires exactly one common evidence period")
    period = next(iter(periods))
    available = [chosen.get((name, period)) for name in ("revenue_growth", "debt_ratio")]
    if not any(available):
        return ()
    growth, debt = available
    for item in available:
        if item is not None and item.unit != "fraction":
            raise ValueError("revenue_growth and debt_ratio require fraction units")
    claims = tuple(Claim(e.metric, e.period, e.value, e.unit, e.id) for e in available if e is not None)
    # Missing metric contributes no signal, not a claimed zero observation.
    g = growth.value if growth else 0.0
    d = debt.value if debt else 0.0
    roles = (("fundamental", 4.0, -1.0, 0.0), ("risk", 1.5, -3.0, 0.2), ("skeptic", 2.0, -2.0, -0.3))
    hypotheses = []
    for name, wg, wd, bias in roles:
        score = max(-30., min(30., wg * g + wd * d + bias))
        probability = 1.0 / (1.0 + math.exp(-score))
        hypotheses.append(Hypothesis(name, probability, "event more likely" if probability >= 0.5 else "event less likely", claims))
    return tuple(hypotheses)


def verify_claim(claim: Claim, research_evidence: Sequence[Evidence], reference_evidence: Sequence[Evidence],
                 as_of: str, tolerance: float = 1e-6) -> dict:
    """Cross-check cited value against a supplied independent-source reference.

    A source name difference is necessary, not proof of independence or truth.
    """
    _validate(research_evidence, as_of)
    _validate(reference_evidence, as_of)
    cited = next((e for e in research_evidence if e.id == claim.evidence_id), None)
    if (cited is None or (cited.metric, cited.period, cited.unit) != (claim.metric, claim.period, claim.unit)
            or not math.isclose(cited.value, claim.value, rel_tol=0.0, abs_tol=tolerance)):
        return {"status": "INVALID_CITATION", "reference_ids": []}
    refs = [e for e in reference_evidence if (e.metric, e.period, e.unit) == (claim.metric, claim.period, claim.unit)
            and e.source != cited.source]
    if not refs:
        return {"status": "UNVERIFIABLE", "reference_ids": []}
    matches = [math.isclose(e.value, claim.value, rel_tol=0.0, abs_tol=tolerance) for e in refs]
    status = "SUPPORTED_BY_REFERENCE" if all(matches) else "CONTRADICTED_BY_REFERENCE"
    return {"status": status, "reference_ids": [e.id for e in refs]}


def report(hypotheses: Sequence[Hypothesis], evidence: Sequence[Evidence], references: Sequence[Evidence],
           as_of: str, config: Config = Config()) -> dict:
    _validate(evidence, as_of)
    _validate(references, as_of)
    if not hypotheses:
        return {"status": "ABSTAIN", "reason": "no usable evidence", "contradictions": contradictions(evidence, config.tolerance)}
    if len({h.agent for h in hypotheses}) != len(hypotheses) or any(not math.isfinite(h.probability) or not 0 <= h.probability <= 1 for h in hypotheses):
        raise ValueError("unique agents with probabilities in [0,1] required")
    probabilities = [h.probability for h in hypotheses]
    distances = [float(jensenshannon([a, 1 - a], [b, 1 - b], base=2) ** 2)
                 for i, a in enumerate(probabilities) for b in probabilities[i + 1:]]
    checks = [{"agent": h.agent, "claim": asdict(c), "verification": verify_claim(c, evidence, references, as_of, config.tolerance)}
              for h in hypotheses for c in h.claims]
    positive = sum(p >= 0.5 for p in probabilities)
    return {"status": "OK", "as_of": as_of, "evidence": [asdict(e) for e in evidence],
            "references": [asdict(e) for e in references], "consensus_probability": float(np.mean(probabilities)),
            "majority_vote_fraction": max(positive, len(probabilities) - positive) / len(probabilities),
            "mean_pairwise_js_divergence_bits": float(np.mean(distances)) if distances else 0.0,
            "contradictions": contradictions(evidence, config.tolerance), "claim_checks": checks,
            "original_hypotheses": [asdict(h) for h in hypotheses],
            "display_hypotheses": [asdict(h) for h in hypotheses] if config.preserve_dissent else [],
            "warnings": ["Rule-based hypotheses are not independent experts; reference matching is not proof of truth or source independence."]}


def benchmark(seed: int = 42, cases: int = 100, config: Config = Config()) -> dict:
    if cases < 1:
        raise ValueError("cases must be positive")
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(cases):
        growth = float(rng.normal(0.1, 0.12))
        debt = float(rng.uniform(0, 0.8))
        event_probability = 1 / (1 + math.exp(-(2 * growth - debt)))
        outcome = int(rng.random() < event_probability)
        time = "2020-01-01T00:00:00Z"
        evidence = [Evidence("g", "source-a", "revenue_growth", "Q1", growth, "fraction", time),
                    Evidence("d", "source-a", "debt_ratio", "Q1", debt, "fraction", time)]
        conflict_injected = bool(rng.random() < 0.5)
        if conflict_injected:
            evidence.append(Evidence("g-conflict", "source-b", "revenue_growth", "Q1", growth + 0.3, "fraction", time))
        references = [Evidence("g-ref", "source-c", "revenue_growth", "Q1", growth, "fraction", time),
                      Evidence("d-ref", "source-c", "debt_ratio", "Q1", debt, "fraction", time)]
        hypotheses = generate_hypotheses(evidence, time, config)
        result = report(hypotheses, evidence, references, time, config)
        if result["status"] != "OK":
            rows.append({"outcome": outcome, "event_probability": event_probability, "evidence": [asdict(e) for e in evidence], "references": [asdict(e) for e in references], "status": result["status"], "injected_conflict": conflict_injected, "report": result})
            continue
        # Baseline: hard-majority forecast rather than probability averaging.
        hard = float(sum(h.probability >= 0.5 for h in hypotheses) >= len(hypotheses) / 2)
        rows.append({"outcome": outcome, "event_probability": event_probability, "evidence": [asdict(e) for e in evidence], "references": [asdict(e) for e in references], "status": "OK", "baseline_probability": hard,
                     "injected_conflict": conflict_injected, "report": result})
    valid = [r for r in rows if r["status"] == "OK"]
    scores = {"brier": float(brier_score_loss([r["outcome"] for r in valid], [r["report"]["consensus_probability"] for r in valid])),
              "baseline_brier": float(brier_score_loss([r["outcome"] for r in valid], [r["baseline_probability"] for r in valid]))} if valid else {"brier": None, "baseline_brier": None}
    return {"seed": seed, "cases": cases, "answered": len(valid), **scores,
            "conflict_detection_rate": float(np.mean([bool(r["report"]["contradictions"]) for r in rows if r["injected_conflict"]])) if any(r["injected_conflict"] for r in rows) else None,
            "records": rows}
