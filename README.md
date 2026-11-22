# InvestIQ research

InvestIQ is an **academic research proposal and exploratory prototype**, not financial advice, a trading system, or a validated forecasting product.

- [Research architecture and novelty validation](docs/investiq-research-design.md)
- [Core algorithms: specifications, schemas, experiments and limitations](docs/core-algorithm-prototypes.md)
- [Earlier synthetic causal benchmark](docs/synthetic-benchmark.md)
- [Temporal evidence infrastructure, ingestion, security and provenance](docs/temporal-evidence-infrastructure.md)
- [Experimental laboratory, MLflow and evaluation protocol](docs/experimental-research-laboratory.md)
- [Unified workspace: setup, staging deployment and limitations](docs/unified-platform.md)
- [Repository audit, completion matrix and implementation roadmap](docs/implementation-audit-and-roadmap.md)
- [Windows local-development setup](docs/windows-local-setup.md)

## Install and test

Python 3.10+; the lock file was tested with Python 3.11.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pytest -q
```

## Run the independent prototypes

```sh
python -m investiq.prototypes.run --algorithm tfsf --seed 42 --size 80 --output results/tfsf.json
python -m investiq.prototypes.run --algorithm eccr --seed 42 --size 2000 --output results/eccr.json
python -m investiq.prototypes.run --algorithm dpro --seed 42 --size 100 --output results/dpro.json
```

Each writes an exploratory experiment record with configurations, sources, versions and results. The documentation lists baselines, ablations, schemas, units and limitations. `results/` is ignored by Git; sample records are in `experiments/records/`. These are not preregistered hypothesis tests or evidence of algorithmic novelty or real-world performance. The earlier standard-library-only causal benchmark is still available via `python -m investiq --seeds 100 --output results/synthetic.json`.

## Evidence infrastructure

The [evidence guide](docs/temporal-evidence-infrastructure.md) covers the Supabase migration, licensed file ingestion, point-in-time snapshots, tenant security and database tests. No remote Supabase project has been changed.

## Experimental research laboratory

Run `python -m investiq.lab --stage selection --output results/lab-selection` for the synthetic, explicitly non-LLM test-double study. See the [laboratory guide](docs/experimental-research-laboratory.md) for locked final evaluation, MLflow tracking, live-provider setup and limitations.
