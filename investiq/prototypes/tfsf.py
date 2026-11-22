"""Temporal financial state fusion: diagonal random-walk Kalman filter."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

DIMENSIONS = ("income", "expenses", "assets", "liabilities", "goal")


@dataclass(frozen=True)
class Observation:
    timestamp: str
    values: Mapping[str, float | None]
    variances: Mapping[str, float]
    currency: str = "USD"


@dataclass(frozen=True)
class Config:
    process_variance: float = 25.0  # per elapsed day, per dimension
    prior_mean: float = 0.0
    prior_variance: float = 1_000_000.0
    change_z: float = 3.0
    use_observation_uncertainty: bool = True
    detect_changes: bool = True

    def __post_init__(self):
        if (not all(math.isfinite(x) for x in (self.process_variance, self.prior_mean, self.prior_variance, self.change_z))
                or self.process_variance < 0 or self.prior_variance <= 0 or self.change_z <= 0):
            raise ValueError("invalid filter configuration")


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    currency: str
    mean: tuple[float, ...]
    variance: tuple[float, ...]
    observed: tuple[str, ...]
    initialized: tuple[str, ...]
    changes: tuple[str, ...]


def _time(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.tz_convert("UTC")


def _validate(observations: list[Observation]) -> list[pd.Timestamp]:
    times = [_time(o.timestamp) for o in observations]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("timestamps must be strictly increasing")
    for obs in observations:
        if len(obs.currency) != 3 or not obs.currency.isascii() or not obs.currency.isalpha() or not obs.currency.isupper() or (observations and obs.currency != observations[0].currency):
            raise ValueError("a single three-letter uppercase currency is required")
        if set(obs.values) - set(DIMENSIONS) or set(obs.variances) - set(DIMENSIONS):
            raise ValueError("unknown state dimension")
        for key, value in obs.values.items():
            if value is not None:
                if not math.isfinite(float(value)) or key not in obs.variances:
                    raise ValueError("observed values require finite values and variances")
                if not math.isfinite(float(obs.variances[key])) or obs.variances[key] <= 0:
                    raise ValueError("observation variances must be positive and finite")
    return times


def fuse(observations: list[Observation], config: Config = Config()) -> tuple[Snapshot, ...]:
    """Filter chronologically; missing fields propagate prior rather than become zero."""
    times = _validate(observations)
    means = np.full(len(DIMENSIONS), config.prior_mean, dtype=float)
    variances = np.full(len(DIMENSIONS), config.prior_variance, dtype=float)
    seen = set()
    snapshots = []
    for index, (obs, timestamp) in enumerate(zip(observations, times)):
        if index:
            variances = variances + config.process_variance * ((timestamp - times[index - 1]).total_seconds() / 86400)
        changed, present = [], []
        for j, key in enumerate(DIMENSIONS):
            value = obs.values.get(key)
            if value is None:
                continue
            present.append(key)
            variance = float(obs.variances[key]) if config.use_observation_uncertainty else 1.0
            innovation = float(value) - means[j]
            if config.detect_changes and key in seen and abs(innovation) / math.sqrt(variances[j] + variance) >= config.change_z:
                changed.append(key)
            gain = variances[j] / (variances[j] + variance)
            means[j] += gain * innovation
            variances[j] *= 1 - gain
            seen.add(key)
        snapshots.append(Snapshot(timestamp.isoformat(), obs.currency, tuple(means), tuple(variances), tuple(present), tuple(k for k in DIMENSIONS if k in seen), tuple(changed)))
    return tuple(snapshots)


def last_observation(observations: list[Observation], config: Config = Config()) -> tuple[Snapshot, ...]:
    """Naive last observation carried forward; prior used before first measurement."""
    times = _validate(observations)
    means = [config.prior_mean] * len(DIMENSIONS)
    variances = [config.prior_variance] * len(DIMENSIONS)
    snapshots = []
    seen = set()
    for obs, timestamp in zip(observations, times):
        present = []
        for j, key in enumerate(DIMENSIONS):
            if obs.values.get(key) is not None:
                present.append(key)
                seen.add(key)
                means[j] = float(obs.values[key])
                variances[j] = float(obs.variances[key])
        snapshots.append(Snapshot(timestamp.isoformat(), obs.currency, tuple(means), tuple(variances), tuple(present), tuple(k for k in DIMENSIONS if k in seen), ()))
    return tuple(snapshots)


def benchmark(seed: int = 42, steps: int = 80, config: Config = Config()) -> dict:
    if steps < 10:
        raise ValueError("steps must be >= 10")
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=steps, freq="D", tz="UTC")
    state = np.array([3000., 2000., 50000., 10000., 100000.])
    truth, inputs = [], []
    break_at = steps // 2
    for t, timestamp in enumerate(dates):
        state = state + rng.normal(0, 5, len(DIMENSIONS))
        if t == break_at:
            state[0] += 600
        truth.append(state.copy())
        present = rng.random(len(DIMENSIONS)) > 0.2
        variances = np.where(rng.random(len(DIMENSIONS)) < 0.25, 40000., 400.)
        values = {k: float(state[i] + rng.normal(0, math.sqrt(variances[i]))) if present[i] else None for i, k in enumerate(DIMENSIONS)}
        inputs.append(Observation(timestamp.isoformat(), values, {k: float(variances[i]) for i, k in enumerate(DIMENSIONS) if present[i]}))
    estimated, naive = fuse(inputs, config), last_observation(inputs, config)
    target = np.array(truth)
    prediction = np.array([s.mean for s in estimated])
    baseline = np.array([s.mean for s in naive])
    std = np.sqrt(np.array([s.variance for s in estimated]))
    return {"seed": seed, "steps": steps, "break_at": break_at,
            "mae": float(mean_absolute_error(target, prediction)), "baseline_mae": float(mean_absolute_error(target, baseline)),
            "coverage_90": float(np.mean(np.abs(prediction - target) <= 1.6448536269514722 * std)),
            "mae_by_dimension": dict(zip(DIMENSIONS, np.mean(np.abs(prediction - target), axis=0).tolist())),
            "baseline_mae_by_dimension": dict(zip(DIMENSIONS, np.mean(np.abs(baseline - target), axis=0).tolist())),
            "income_break_detected_at_break": "income" in estimated[break_at].changes,
            "truth": target.tolist(), "observations": [{"timestamp": o.timestamp, "values": dict(o.values), "variances": dict(o.variances), "currency": o.currency} for o in inputs],
            "snapshots": [{"timestamp": s.timestamp, "currency": s.currency, "mean": list(s.mean), "variance": list(s.variance), "observed": list(s.observed), "initialized": list(s.initialized), "changes": list(s.changes)} for s in estimated],
            "baseline_snapshots": [{"timestamp": s.timestamp, "currency": s.currency, "mean": list(s.mean), "variance": list(s.variance), "observed": list(s.observed), "initialized": list(s.initialized), "changes": list(s.changes)} for s in naive]}
