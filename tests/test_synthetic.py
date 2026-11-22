import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from investiq.synthetic import (GeneratorConfig, benchmark, fit_effect, gate,
                                generate, oracle_supported, quantile, true_effect)


class GeneratorTests(unittest.TestCase):
    def test_seed_reproducibility_and_no_future_in_prefix(self):
        first = generate(GeneratorConfig(steps=20), 17)
        self.assertEqual(first, generate(GeneratorConfig(steps=20), 17))
        self.assertEqual(first, generate(GeneratorConfig(steps=30), 17)[:20])
        self.assertNotEqual(first, generate(GeneratorConfig(steps=20), 18))
        self.assertNotIn("latent", first[0])

    def test_break_time_and_invalid_config(self):
        before = generate(GeneratorConfig(steps=20), 0)
        broken = generate(GeneratorConfig(steps=20, beta_after_break=1.0, break_at=10), 0)
        self.assertEqual(before[:10], broken[:10])
        self.assertNotEqual(before[10]["y"], broken[10]["y"])
        self.assertEqual(true_effect(GeneratorConfig(beta_after_break=1.0, break_at=100), -2), -2)
        for kwargs in ({"steps": 9}, {"break_at": 3}, {"beta": float("nan")},
                       {"beta_after_break": 1, "break_at": 201}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                GeneratorConfig(**kwargs)

    def test_oracle_paired_interventions_and_estimators(self):
        # Shared exogenous inputs yield the exact one-step structural effect.
        rng = random.Random(19)
        changes = []
        for _ in range(100):
            previous_y, c, latent, noise = (rng.gauss(0, 1) for _ in range(4))
            def outcome(a):
                return 0.4 * previous_y + 0.5 * a + 0.7 * c + latent + noise
            changes.append(outcome(1.7) - outcome(0.0))
        self.assertTrue(all(math.isclose(x, 0.85, abs_tol=1e-12) for x in changes))
        # Simulation check, not a proof of identification.
        estimates = [fit_effect(generate(GeneratorConfig(), seed)) for seed in range(50)]
        self.assertAlmostEqual(sum(estimates) / len(estimates), 0.5, delta=0.04)

    def test_known_hidden_confounding_bias(self):
        configs = (GeneratorConfig(), GeneratorConfig(hidden_strength=1.0))
        means = [sum(fit_effect(generate(config, seed)) for seed in range(60)) / 60 for config in configs]
        self.assertGreater(means[1] - means[0], 0.25)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.rows = generate(GeneratorConfig(), 4)

    def test_quantile_and_bad_design(self):
        self.assertEqual(quantile([0, 10], 0.25), 2.5)
        with self.assertRaises(ValueError):
            quantile([], 0.5)
        with self.assertRaises(ValueError):
            fit_effect([{"a": 1, "c": 1, "y_lag": 1, "y": 1}] * 10)

    def test_gate_constraints_support_and_assumptions(self):
        self.assertEqual(gate(self.rows, 1, 0.5, (1, 0), True)["status"], "INCONSISTENT_CONSTRAINTS")
        self.assertEqual(gate(self.rows, 1, 0.5, (0.2, 0.8), False)["status"], "ABSTAIN_CAUSAL")
        self.assertEqual(gate(self.rows, 100, 0.5, (0.2, 0.8), True)["status"], "ABSTAIN_CAUSAL")
        result = gate(self.rows, -1, 0.9, (0.2, 0.8), True)
        self.assertEqual(result["estimate"], -0.8)
        self.assertEqual(result["sensitivity_range"], [-0.8, -0.2])
        self.assertTrue(oracle_supported(GeneratorConfig(), 1))
        self.assertFalse(oracle_supported(GeneratorConfig(), 3))

    def test_summary_denominators_and_disclosed_hidden(self):
        report = benchmark(3)
        group = report["results"]["hidden_confounder_disclosed"]
        self.assertEqual(group["total"], 15)
        self.assertEqual(group["supported_answer_rate"], 0)
        self.assertEqual(group["paired_supported_count"], 0)
        self.assertIsNone(group["paired_gate_mae"])
        self.assertEqual(group["supported_total"] + group["unsupported_total"], 15)
        with self.assertRaises(ValueError):
            benchmark(0)

    def test_cli_writes_replayable_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            result = subprocess.run([sys.executable, "-m", "investiq", "--seeds", "2", "--output", str(path)],
                                    check=True, capture_output=True, text=True)
            report = json.loads(path.read_text())
            self.assertIn("Exploratory", result.stdout)
            self.assertEqual(report["status"], "exploratory_not_hypothesis_test")
            self.assertEqual(report["seed_count"], 2)
            self.assertEqual(len(report["environment"]["synthetic_source_sha256"]), 64)
            self.assertEqual(report["results"]["observed_correct_evidence"]["total"], 10)


if __name__ == "__main__":
    unittest.main()
