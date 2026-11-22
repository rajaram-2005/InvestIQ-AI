# Exploratory synthetic causal benchmark

**Status:** implementation feasibility only; not the preregistered H2 experiment and not an empirical claim about financial markets. The [research design](investiq-research-design.md) remains the governing proposal. No external data are used. This experiment is deliberately small enough to run with Python 3.10+ and the standard library.

## What is implemented

For each seed we generate 200 observations after a 50-step burn-in, with stationary initial C. The equations are `C[t] = 0.6 C[t-1] + eC`, `A[t] = 0.5 C[t] + h L[t] + eA`, and `Y[t] = 0.4 Y[t-1] + beta[t] A[t] + 0.7 C[t] + h L[t] + eY`, with independent standard-normal innovations and latent `L`. The estimator does not receive `L`. Starting from the same prior state and exogenous variables, replacing A by `a` rather than 0 produces a one-step causal **contrast** `beta * a`. This is not the absolute interventional expectation specified for H2 in the research design. The oracle knows the generator, not information available in a real-world study.

Five scenarios, all exploratory, use seeds 0 through N−1 and the same five actions `[-3, -1, 0.5, 1, 3]`:

1. Observed confounder with an illustrative coefficient constraint `[0.2, 0.8]` containing the true beta 0.5.
2. Same data, corrupted coefficient constraint `[0.8, 1.0]`, excluding the true beta.
3. Hidden confounder with unapproved assumptions: the gate abstains.
4. Hidden confounder wrongly approved: illustrates that an assertion is not identification.
5. Coefficient changes from 0.5 to 1.0 at observation 100; the static assumption `[0.2, 0.8]` is wrong for the next step. All 200 historical observations are used, so this is an intentionally misspecified pooled fit.

The unadjusted baseline is OLS of Y on A; the adjusted baseline adds C and lagged Y. The illustrative gate first checks an externally provided boolean assumption approval, then checks whether action falls inside the empirical 5th–95th percentile of historical A, then clips the adjusted coefficient to the supplied range. It returns an interval obtained by multiplying the range endpoints by the action. A malformed/contradictory range is rejected; an approved but incorrect range cannot be detected in general. These are assumption-dependent sensitivity ranges, **not identified bounds, confidence intervals, or evidence of causality**. Marginal support is not conditional positivity, and clipping may introduce severe bias. Approval is a deliberately visible input, not an automated verification or a source-backed evidence registry.

## Outputs and interpretation

The JSON includes each seed/action, truth, both baseline estimates, gate status and, where answered, gate estimate and sensitivity range. Per-scenario summaries include the number of records, population-supported and unsupported counts, supported answer rate, unsupported acceptance rate, paired MAEs for both baseline models and the gate, and paired sensitivity-range coverage and width. A `null` mean means its denominator is zero. MAEs and range metrics use **only oracle-supported and gate-answered pairs**; they do not represent all-origin performance. The oracle support threshold is the population 5th–95th marginal normal range of A for the known generator, whereas the gate uses an empirical historical percentile range; disagreement is expected.

These outputs do not run confidence intervals, multiple-testing corrections, preregistration, blind analyses or the H2 acceptance criterion. Seeds are shared across scenarios to support paired comparisons; they are not presented as a held-out evaluation. Do not select a scenario, constraint, seed count or threshold based on observed results and then call it confirmatory. Misapproved confounding, wrong bounds, model breaks and support failures are intentionally retained, not hidden.

## Reproduction and checks

```sh
python3 -m unittest discover -s tests -v
python3 -m investiq --seeds 100 --output results/synthetic.json
```

The report records Python version, simulation parameters, source hash, seed rule, individual records and aggregated values. Run with the same Python version and source for replay; floating-point output can differ across runtimes or platforms. `results/` is ignored by Git. Save copies outside that folder only if they are meant to be reviewed/versioned. Tests cover deterministic generation, temporal prefix, coefficient break, an analytically paired oracle contrast, simulated regression behavior, constraint and support gates, abstention, denominators and the CLI. The analytical oracle check validates this toy equation, not a financial causal model.

Before a confirmatory H2 study: complete the full literature search and preregistration; define independent development and test seeds, the absolute interventional estimand, coverage/width and abstention denominators, supported query population and all-origin fallback; include comparable standard partial-identification/sensitivity baselines, confidence intervals resampled by seed/scenario, power calculations and Holm correction as specified in the main design. Build a real evidence registry and review policy before describing constraints as evidence-linked. No real interventions or financial data are included here.
