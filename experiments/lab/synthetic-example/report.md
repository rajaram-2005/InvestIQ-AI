# InvestIQ exploratory experiment

Dataset: `synthetic-panel-v1-seed-42-days-60`; stage: `final`; backend: `synthetic-heuristic-not-an-llm`; seed: 42.

**Not a claim of real-world performance.** See report.json for all configuration, exclusions, uncertainty and per-record results.

```text
         n    mae   rmse  brier  log_loss  expected_calibration_error  maximum_drawdown  evidence_coverage  unsupported_claim_rate  scenario_error  scenario_scored_n  risk_constraint_violations  cost_per_research_report_usd
System
E0      45 0.0175 0.0294 0.2473    0.6877                      0.0198            0.0692             1.0000                  0.0000          0.0030                 45                           0                        0.0000
E1      45 0.0167 0.0276 0.2452    0.6836                      0.1341            0.0122             0.8889                  0.1111          0.0010                 45                           0                        0.0000
E2      45 0.0167 0.0276 0.2417    0.6765                      0.1088            0.0091             1.0000                  0.0519          0.0010                 45                           0                        0.0000
E3      45 0.0167 0.0276 0.2417    0.6765                      0.1088            0.0091             1.0000                  0.0519          0.0010                 45                           0                        0.0000
E4      45 0.0171 0.0289 0.2450    0.6831                      0.0481            0.0601             1.0000                  0.0519          0.0000                 45                           0                        0.0000
```

## Paired Brier comparison versus E0
```text
        paired_brier_difference  ci_lower  ci_upper  p_two_sided  p_holm  dates  common_rows
System
E1                      -0.0019   -0.0056    0.0008       0.3357  0.3357     14           45
E2                      -0.0051   -0.0105    0.0007       0.0789  0.2368     14           45
E3                      -0.0051   -0.0105    0.0007       0.0789  0.2368     14           45
E4                      -0.0023   -0.0047   -0.0007       0.0310  0.1239     14           45
```

## Negative results
- E1 has worse expected_calibration_error than E0 on the final set (descriptive).
- E1 has worse unsupported_claim_rate than E0 on the final set (descriptive).
- E2 has worse expected_calibration_error than E0 on the final set (descriptive).
- E2 has worse unsupported_claim_rate than E0 on the final set (descriptive).
- E3 has worse expected_calibration_error than E0 on the final set (descriptive).
- E3 has worse unsupported_claim_rate than E0 on the final set (descriptive).
- E4 has worse expected_calibration_error than E0 on the final set (descriptive).
- E4 has worse unsupported_claim_rate than E0 on the final set (descriptive).
- E1 has lower evidence coverage than E0 on the final set (descriptive).

## Limitations
- E1–E4 use heuristic test doubles, NOT actual LLMs or agents.
- Synthetic scenario truth uses the same 0.3 slope assumed by E4: scenario error is a wiring check, not a blinded performance comparison.
- Exploratory; no result establishes originality or financial performance.
- E4 is a limited integration prototype, not the complete production architecture.
- Scenario coefficient is assumed, not identified; synthetic scenario ground truth is not real causal validation.
- Historical LLM model weights may contain future information even with as-of tool inputs.
- Membership/delisting and source-license assertions require independent audit.
- Evidence matching is a numeric rule, not semantic fact verification.
- Reported provider cost excludes compute, staff, infrastructure and unknown token pricing.
- Portfolio is illustrative, uses simplified daily turnover/costs, and is not executable investment advice.
- Bootstrap p-values and intervals are exploratory and sensitive to block length and few independent dates.
