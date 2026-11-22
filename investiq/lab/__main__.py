import argparse
import json
from pathlib import Path
from .data import synthetic
from .runner import ABLATIONS, Config, run_experiment


def main():
    parser = argparse.ArgumentParser(description='Run a fixed chronological InvestIQ research protocol')
    parser.add_argument('--dataset', type=Path, help='JSON panel; omit for a synthetic fixture')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--days', type=int, default=60, help='synthetic fixture size')
    parser.add_argument('--train-end'); parser.add_argument('--validation-end'); parser.add_argument('--test-end')
    parser.add_argument('--backend', choices=('simulated','live'), default='simulated')
    parser.add_argument('--stage', choices=('selection','final'), default='final')
    parser.add_argument('--model-version', default='synthetic-heuristic-v1-not-an-llm')
    parser.add_argument('--input-usd-per-million', type=float); parser.add_argument('--output-usd-per-million', type=float)
    parser.add_argument('--transaction-cost-bps', type=float, default=10.0)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    parser.add_argument('--bootstrap-block-length', type=int, default=5)
    parser.add_argument('--monte-carlo-draws', type=int, default=128)
    parser.add_argument('--ablation', choices=ABLATIONS, default='none')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.dataset:
        dataset = json.loads(args.dataset.read_text(encoding='utf-8'))
        cutoffs = {'train_end': args.train_end, 'validation_end': args.validation_end, 'test_end': args.test_end}
        if any(v is None for v in cutoffs.values()): parser.error('dataset input requires all split cutoffs')
    else:
        dataset, cutoffs = synthetic(args.seed, args.days)
        for name in cutoffs:
            if getattr(args, name) is not None: cutoffs[name] = getattr(args, name)
    config = Config(**cutoffs, seed=args.seed, model_version=args.model_version, backend=args.backend, stage=args.stage,
                    transaction_cost_bps=args.transaction_cost_bps, bootstrap_samples=args.bootstrap_samples,
                    bootstrap_block_length=args.bootstrap_block_length, monte_carlo_draws=args.monte_carlo_draws,
                    ablation=args.ablation, input_usd_per_million=args.input_usd_per_million,
                    output_usd_per_million=args.output_usd_per_million)
    report = run_experiment(dataset, config, args.output)
    print(f"Wrote {report['dataset_kind']} experiment report to {args.output}")


if __name__ == '__main__':
    main()
