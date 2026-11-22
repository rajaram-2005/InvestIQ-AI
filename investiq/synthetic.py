"""Small, reproducible, deliberately limited structural causal benchmark.

The gate and bounds are illustrative assumptions, not identified causal bounds.
No external data or non-standard Python packages are required.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
from typing import Any


@dataclass(frozen=True)
class GeneratorConfig:
    steps: int = 200
    beta: float = 0.5
    beta_after_break: float | None = None
    break_at: int | None = None
    hidden_strength: float = 0.0
    burn_in: int = 50

    def __post_init__(self) -> None:
        if self.steps < 10 or self.burn_in < 0:
            raise ValueError("steps must be >= 10 and burn_in must be >= 0")
        if not math.isfinite(self.beta) or not math.isfinite(self.hidden_strength):
            raise ValueError("parameters must be finite")
        if self.beta_after_break is not None and not math.isfinite(self.beta_after_break):
            raise ValueError("beta_after_break must be finite")
        if (self.break_at is None) != (self.beta_after_break is None):
            raise ValueError("break_at and beta_after_break must be supplied together")
        if self.break_at is not None and not 0 <= self.break_at <= self.steps:
            raise ValueError("break_at must fall between 0 and steps")


def generate(config: GeneratorConfig, seed: int) -> list[dict[str, float]]:
    """Generate observed history; C and L have no persistence through Y or A.

    The break is indexed relative to returned observations, not burn-in. The
    one-step target after the history uses beta_after_break when break_at is set.
    Hidden L is deliberately not returned to the estimator.
    """
    rng = random.Random(seed)
    c = rng.gauss(0.0, 1.0 / math.sqrt(1.0 - 0.6**2))
    y = 0.0
    rows = []
    for index in range(-config.burn_in, config.steps):
        c = 0.6 * c + rng.gauss(0.0, 1.0)
        latent = rng.gauss(0.0, 1.0)
        a = 0.5 * c + config.hidden_strength * latent + rng.gauss(0.0, 1.0)
        beta = (config.beta_after_break if config.break_at is not None
                and index >= config.break_at else config.beta)
        next_y = 0.4 * y + beta * a + 0.7 * c + config.hidden_strength * latent + rng.gauss(0.0, 1.0)
        if index >= 0:
            rows.append({"c": c, "a": a, "y_lag": y, "y": next_y})
        y = next_y
    return rows


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a small linear system with partial pivoting; reject singular fits."""
    n = len(vector)
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    scale = max(abs(value) for row in matrix for value in row)
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) <= max(scale, 1.0) * 1e-12:
            raise ValueError("singular regression design")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        factor = augmented[col][col]
        augmented[col] = [value / factor for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [v - factor * p for v, p in zip(augmented[row], augmented[col])]
    return [augmented[row][-1] for row in range(n)]


def fit_effect(rows: list[dict[str, float]], adjusted: bool = True) -> float:
    """OLS treatment coefficient. Association is not automatically a causal effect."""
    columns = ("a", "c", "y_lag") if adjusted else ("a",)
    if len(rows) <= len(columns) + 1:
        raise ValueError("not enough observations")
    xs = [[1.0] + [row[name] for name in columns] for row in rows]
    n = len(xs[0])
    xtx = [[sum(x[i] * x[j] for x in xs) for j in range(n)] for i in range(n)]
    xty = [sum(x[i] * row["y"] for x, row in zip(xs, rows)) for i in range(n)]
    return _solve(xtx, xty)[1]


def quantile(values: list[float], p: float) -> float:
    if not values or not 0 <= p <= 1:
        raise ValueError("quantile requires values and p in [0, 1]")
    values = sorted(values)
    position = (len(values) - 1) * p
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def gate(rows: list[dict[str, float]], action: float, coefficient: float,
         bounds: tuple[float, float], assumptions_approved: bool) -> dict[str, Any]:
    """Illustrative marginal-support gate and externally asserted coefficient range.

    Input approval is a human/config assertion, not an automatic causal check.
    The range is a sensitivity range, with no statistical coverage guarantee.
    """
    lower, upper = bounds
    if not all(math.isfinite(x) for x in (action, coefficient, lower, upper)) or lower > upper:
        return {"status": "INCONSISTENT_CONSTRAINTS"}
    if not assumptions_approved:
        return {"status": "ABSTAIN_CAUSAL", "reason": "assumptions not approved"}
    support = (quantile([r["a"] for r in rows], 0.05), quantile([r["a"] for r in rows], 0.95))
    if not support[0] <= action <= support[1]:
        return {"status": "ABSTAIN_CAUSAL", "reason": "outside empirical marginal support", "support": support}
    clipped = min(max(coefficient, lower), upper)
    return {"status": "OK", "estimate": clipped * action,
            "sensitivity_range": sorted([lower * action, upper * action]), "support": support}


def true_effect(config: GeneratorConfig, action: float) -> float:
    beta = config.beta_after_break if config.break_at is not None else config.beta
    assert beta is not None
    return beta * action


def oracle_supported(config: GeneratorConfig, action: float) -> bool:
    """Synthetic-only 5–95% marginal population support, not conditional positivity."""
    sd = math.sqrt(0.25 / (1 - 0.6**2) + 1 + config.hidden_strength**2)
    return abs(action) <= 1.6448536269514722 * sd


def benchmark(seeds: int = 100) -> dict[str, Any]:
    if not 1 <= seeds <= 10000:
        raise ValueError("seeds must be between 1 and 10000")
    actions = [-3.0, -1.0, 0.5, 1.0, 3.0]
    scenarios = {
        "observed_correct_evidence": (GeneratorConfig(), (0.2, 0.8), True),
        "observed_corrupt_evidence": (GeneratorConfig(), (0.8, 1.0), True),
        "hidden_confounder_disclosed": (GeneratorConfig(hidden_strength=1.0), (0.2, 0.8), False),
        "hidden_confounder_misapproved": (GeneratorConfig(hidden_strength=1.0), (0.2, 0.8), True),
        "coefficient_break": (GeneratorConfig(beta_after_break=1.0, break_at=100), (0.2, 0.8), True),
    }
    results = {}
    for name, (config, bounds, approved) in scenarios.items():
        records = []
        for seed in range(seeds):
            rows = generate(config, seed)
            adjusted = fit_effect(rows)
            naive = fit_effect(rows, adjusted=False)
            for action in actions:
                truth = true_effect(config, action)
                result = gate(rows, action, adjusted, bounds, approved)
                records.append({"seed": seed, "action": action, "truth": truth,
                                "oracle_supported": oracle_supported(config, action),
                                "adjusted_estimate": adjusted * action,
                                "naive_estimate": naive * action, "gate": result})
        supported = [r for r in records if r["oracle_supported"]]
        unsupported = [r for r in records if not r["oracle_supported"]]
        paired = [r for r in supported if r["gate"]["status"] == "OK"]
        def mean(items: list[float]) -> float | None:
            return sum(items) / len(items) if items else None
        results[name] = {
            "generator": asdict(config), "coefficient_assumption": list(bounds),
            "assumptions_approved": approved, "total": len(records),
            "supported_total": len(supported), "supported_answer_rate": mean([float(r["gate"]["status"] == "OK") for r in supported]),
            "unsupported_total": len(unsupported), "unsupported_acceptance_rate": mean([float(r["gate"]["status"] == "OK") for r in unsupported]),
            "paired_supported_count": len(paired),
            "paired_adjusted_mae": mean([abs(r["adjusted_estimate"] - r["truth"]) for r in paired]),
            "paired_naive_mae": mean([abs(r["naive_estimate"] - r["truth"]) for r in paired]),
            "paired_gate_mae": mean([abs(r["gate"]["estimate"] - r["truth"]) for r in paired]),
            "paired_sensitivity_contains_truth": mean([float(r["gate"]["sensitivity_range"][0] <= r["truth"] <= r["gate"]["sensitivity_range"][1]) for r in paired]),
            "paired_sensitivity_width": mean([r["gate"]["sensitivity_range"][1] - r["gate"]["sensitivity_range"][0] for r in paired]),
            "records": records,
        }
    return {"format_version": 1, "status": "exploratory_not_hypothesis_test", "seed_count": seeds,
            "seed_rule": "integers 0 through seed_count-1, shared across scenarios for paired comparison",
            "actions": actions, "results": results}
