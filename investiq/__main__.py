"""Run the exploratory simulation and write a reproducible JSON artifact."""
import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

from . import __version__
from .synthetic import benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100, help="number of independent generator seeds (1–10000)")
    parser.add_argument("--output", type=Path, default=Path("results/synthetic.json"))
    args = parser.parse_args()
    if not 1 <= args.seeds <= 10000:
        parser.error("--seeds must be between 1 and 10000")
    report = benchmark(args.seeds)
    report["environment"] = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                             "investiq_version": __version__,
                             "synthetic_source_sha256": hashlib.sha256(Path(__file__).with_name("synthetic.py").read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Exploratory report written to {args.output}")
    for name, result in report["results"].items():
        print(f"{name}: answered supported={result['supported_answer_rate']}, "
              f"paired gate MAE={result['paired_gate_mae']}, unsupported accepted={result['unsupported_acceptance_rate']}")


if __name__ == "__main__":
    main()
