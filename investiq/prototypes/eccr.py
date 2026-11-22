"""Evidence-linked linear Gaussian structural scenario simulation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Mapping

import networkx as nx
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    available_at: str


@dataclass(frozen=True)
class Observation:
    variable: str
    value: float
    unit: str
    evidence_id: str


@dataclass(frozen=True)
class Node:
    name: str
    unit: str
    intercept: float
    noise_std: float
    parents: Mapping[str, float] = field(default_factory=dict)
    assumption_evidence: tuple[str, ...] = ()
    allowed_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class Intervention:
    value: float
    unit: str


@dataclass(frozen=True)
class Scenario:
    as_of: str
    nodes: tuple[Node, ...]
    evidence: tuple[Evidence, ...]
    observations: tuple[Observation, ...]
    intervention: Mapping[str, Intervention]
    assumptions_approved: bool
    no_unmeasured_confounding_assumed: bool


@dataclass(frozen=True)
class Config:
    draws: int = 2000
    seed: int = 42
    enforce_approval: bool = True
    enforce_support: bool = True
    structural_edges: bool = True

    def __post_init__(self):
        if not 1 <= self.draws <= 1_000_000 or not 0 <= self.seed < 2**64:
            raise ValueError("invalid draws or seed")


def _utc(value: str) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.tz_convert("UTC")


def validate(scenario: Scenario, config: Config = Config()) -> tuple[list[str], list[str]]:
    """Structural errors always block. Policy ablations remain non-causal exercises."""
    errors, warnings = [], []
    try:
        cutoff = _utc(scenario.as_of)
    except (ValueError, TypeError):
        return ["invalid as_of"], []
    ids = [e.id for e in scenario.evidence]
    if len(set(ids)) != len(ids) or any(not e.id or not e.source for e in scenario.evidence):
        errors.append("evidence identifiers and sources must be nonempty and unique")
    valid_evidence = set()
    for evidence in scenario.evidence:
        try:
            if _utc(evidence.available_at) > cutoff:
                errors.append(f"future evidence: {evidence.id}")
            else:
                valid_evidence.add(evidence.id)
        except (ValueError, TypeError):
            errors.append(f"invalid evidence time: {evidence.id}")
    nodes = {node.name: node for node in scenario.nodes}
    if not nodes or len(nodes) != len(scenario.nodes) or any(not n.name or not n.unit for n in scenario.nodes):
        errors.append("node names must be nonempty and unique, with units")
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for node in scenario.nodes:
        if not math.isfinite(node.intercept) or not math.isfinite(node.noise_std) or node.noise_std < 0:
            errors.append(f"invalid parameters: {node.name}")
        if not node.assumption_evidence or not set(node.assumption_evidence) <= valid_evidence:
            errors.append(f"missing assumption evidence: {node.name}")
        if node.allowed_range is not None:
            lo, hi = node.allowed_range
            if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
                errors.append(f"invalid range: {node.name}")
        for parent, coefficient in node.parents.items():
            if parent not in nodes or not math.isfinite(coefficient):
                errors.append(f"invalid parent or coefficient: {node.name}/{parent}")
            else:
                graph.add_edge(parent, node.name)
    if not nx.is_directed_acyclic_graph(graph):
        errors.append("causal graph must be acyclic")
    for observation in scenario.observations:
        if (observation.variable not in nodes or observation.evidence_id not in valid_evidence
                or not math.isfinite(observation.value)
                or (observation.variable in nodes and observation.unit != nodes[observation.variable].unit)):
            errors.append(f"invalid observation: {observation.variable}")
    for variable, intervention in scenario.intervention.items():
        value = intervention.value
        if variable not in nodes or not math.isfinite(value) or (variable in nodes and intervention.unit != nodes[variable].unit):
            errors.append(f"invalid intervention: {variable}")
        elif nodes[variable].allowed_range is None:
            if config.enforce_support:
                errors.append(f"intervention range missing: {variable}")
            else:
                warnings.append(f"intervention range not checked: {variable}")
        elif not nodes[variable].allowed_range[0] <= value <= nodes[variable].allowed_range[1]:
            if config.enforce_support:
                errors.append(f"intervention outside declared range: {variable}")
            else:
                warnings.append(f"intervention outside declared range: {variable}")
    if not scenario.intervention:
        errors.append("intervention required")
    if not scenario.assumptions_approved or not scenario.no_unmeasured_confounding_assumed:
        if config.enforce_approval:
            errors.append("causal assumptions not approved")
        else:
            warnings.append("unapproved causal assumptions; hypothetical only")
    if not config.structural_edges:
        warnings.append("independent-node baseline ignores causal edges; hypothetical only")
    if not config.enforce_support:
        warnings.append("support checks disabled; hypothetical only")
    if not config.enforce_approval:
        warnings.append("assumption checks disabled; hypothetical only")
    return errors, warnings


def simulate(scenario: Scenario, config: Config = Config()) -> dict:
    errors, warnings = validate(scenario, config)
    if errors:
        return {"status": "ABSTAIN", "errors": errors, "warnings": warnings}
    nodes = {node.name: node for node in scenario.nodes}
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from((parent, node.name) for node in scenario.nodes for parent in node.parents)
    order = list(nx.lexicographical_topological_sort(graph))
    rng = np.random.default_rng(config.seed)
    noise = {name: rng.normal(0.0, nodes[name].noise_std, config.draws) for name in order}
    factual, altered = {}, {}
    for name in order:
        node = nodes[name]
        parents = node.parents if config.structural_edges else {}
        factual[name] = node.intercept + noise[name] + sum(coef * factual[p] for p, coef in parents.items())
        if name in scenario.intervention:
            altered[name] = np.full(config.draws, scenario.intervention[name].value, dtype=float)
        else:
            altered[name] = node.intercept + noise[name] + sum(coef * altered[p] for p, coef in parents.items())
    distributions = {}
    for name in order:
        delta = altered[name] - factual[name]
        distributions[name] = {"unit": nodes[name].unit, "samples": altered[name].tolist(),
                               "paired_delta_samples": delta.tolist(),
                               "mean": float(np.mean(altered[name])),
                               "quantile_05_95": np.quantile(altered[name], [0.05, 0.95]).tolist(),
                               "mean_paired_delta": float(np.mean(delta))}
    return {"status": "OK", "interpretation": "model_conditional_hypothetical" if warnings else "model_conditional_intervention",
            "warnings": warnings, "topological_order": order, "draws": config.draws, "seed": config.seed,
            "as_of": scenario.as_of, "evidence": [asdict(e) for e in scenario.evidence],
            "nodes": [asdict(n) for n in scenario.nodes],
            "intervention": {key: asdict(value) for key, value in scenario.intervention.items()},
            "evidence_ids": sorted(e.id for e in scenario.evidence),
            "assumption_evidence": {node.name: list(node.assumption_evidence) for node in scenario.nodes},
            "observations": [vars(o) for o in scenario.observations], "distributions": distributions}


def example_scenario() -> Scenario:
    evidence = (Evidence("doc-1", "synthetic:scenario-assumptions", "2020-01-01T00:00:00Z"),)
    nodes = (Node("income", "USD/month", 3000, 100, assumption_evidence=("doc-1",), allowed_range=(2000, 5000)),
             Node("expense", "USD/month", 2000, 80, assumption_evidence=("doc-1",), allowed_range=(1000, 4000)),
             Node("savings", "USD/month", 0, 30, {"income": 1.0, "expense": -1.0}, ("doc-1",)))
    return Scenario("2020-01-02T00:00:00Z", nodes, evidence,
                    (Observation("income", 3000, "USD/month", "doc-1"),), {"income": Intervention(3500, "USD/month")}, True, True)


def benchmark(seed: int = 42, draws: int = 2000, config: Config | None = None) -> dict:
    """Known linear model: compare treatment contrast against analytic oracle."""
    scenario = example_scenario()
    cfg = config or Config(seed=seed, draws=draws)
    model = simulate(scenario, cfg)
    baseline = simulate(scenario, Config(draws=cfg.draws, seed=cfg.seed, structural_edges=False))
    # E[Y|do(income=3500)] - E[Y|do(income=3000)] = 500.
    oracle = 500.0
    if model["status"] != "OK":
        return {"status": "ABSTAIN", "scenario": asdict(scenario), "model": model, "baseline": baseline, "oracle_contrast": oracle}
    # Factual expectation E[income] is 3000, hence mean paired delta estimates the oracle contrast.
    estimate = model["distributions"]["savings"]["mean_paired_delta"]
    baseline_estimate = baseline["distributions"]["savings"]["mean_paired_delta"]
    return {"status": "OK", "scenario": asdict(scenario), "oracle_contrast": oracle, "estimate": estimate,
            "absolute_error": abs(estimate - oracle), "baseline_estimate": baseline_estimate,
            "baseline_absolute_error": abs(baseline_estimate - oracle), "model": model, "baseline": baseline}
