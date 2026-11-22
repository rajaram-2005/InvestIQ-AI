"""Write exploratory, versioned, replayable experiment records."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import subprocess
import sys

from . import dpro, eccr, tfsf

ABLATIONS = {
    "tfsf": {"none": {}, "ignore_uncertainty": {"use_observation_uncertainty": False}, "no_change_detection": {"detect_changes": False}},
    "eccr": {"none": {}, "independent_nodes": {"structural_edges": False}, "no_support_check": {"enforce_support": False}, "no_approval_check": {"enforce_approval": False}},
    "dpro": {"none": {}, "hide_dissent": {"preserve_dissent": False}, "exclude_conflicts": {"exclude_conflicting_metrics": True}},
}


def run(algorithm: str, ablation: str, seed: int, size: int) -> dict:
    if algorithm not in ABLATIONS or ablation not in ABLATIONS[algorithm]:
        raise ValueError("invalid algorithm/ablation")
    if not 0 <= seed < 2**64 or not 10 <= size <= 10000:
        raise ValueError("seed must be in [0, 2**64), size in [10, 10000]")
    overrides = ABLATIONS[algorithm][ablation]
    if algorithm == "tfsf":
        config = tfsf.Config(**overrides)
        result = tfsf.benchmark(seed, size, config)
    elif algorithm == "eccr":
        config = eccr.Config(seed=seed, draws=size, **overrides)
        result = eccr.benchmark(config=config)
    else:
        config = dpro.Config(**overrides)
        result = dpro.benchmark(seed, size, config)
    sources = {}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        sources[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit = None
    versions = {p: importlib.metadata.version(p) for p in ("numpy", "pandas", "scipy", "scikit-learn", "networkx", "pytest")}
    return {"record_version": 1, "purpose": "exploratory synthetic benchmark; not a hypothesis test",
            "algorithm": algorithm, "ablation": ablation, "config": asdict(config),
            "seed": seed, "size": size, "source_sha256": sources, "git_commit": commit,
            "environment": {"python": platform.python_version(), "implementation": platform.python_implementation(), "packages": versions},
            "result": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", required=True, choices=ABLATIONS)
    parser.add_argument("--ablation", default="none")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = run(args.algorithm, args.ablation, args.seed, args.size)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Wrote exploratory {args.algorithm} record: {args.output}")


if __name__ == "__main__":
    main()
