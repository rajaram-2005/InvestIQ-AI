# InvestIQ core research prototypes

**Status:** executable exploratory prototypes, not financial advice, production systems, or evidence of algorithmic originality. The algorithms use familiar filters, structural equations and ensemble diagnostics; names are project labels, not novelty claims. The [research design and literature review](investiq-research-design.md) defines the broader hypotheses; these prototypes do **not** constitute their confirmatory tests. The investor-state setting here is a separate prototype scope from the issuer-forecasting primary study described there.

## Installation and experiments

Python 3.10+ is required (the included lock was generated and tested with Python 3.11). In the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pytest -q

python -m investiq.prototypes.run --algorithm tfsf --seed 42 --size 80 --output results/tfsf.json
python -m investiq.prototypes.run --algorithm eccr --seed 42 --size 2000 --output results/eccr.json
python -m investiq.prototypes.run --algorithm dpro --seed 42 --size 100 --output results/dpro.json
# Example ablation, applied one at a time:
python -m investiq.prototypes.run --algorithm tfsf --ablation ignore_uncertainty --seed 42 --size 80 --output results/tfsf-ablation.json
```

`size` means time steps for TFSF, Monte Carlo draws for ECCR, and cases for DPRO; range 10–10,000 in this runner. Direct ECCR API allows up to one million draws, so callers must manage memory. Three small sample records are in `experiments/records/`; generated `results/` files are ignored. A record includes the algorithm and ablation, full configuration, seed/size, per-module SHA-256 source hashes, git HEAD (which may not include uncommitted changes), Python and package versions, generated inputs and individual results. Source hashes, not HEAD alone, identify the working-tree implementation. `requirements-lock.txt` pins the tested direct and transitive Python packages; platform and BLAS differences may affect numerical replay. Exact JSON replay is expected with the same source and environment, not guaranteed across platforms. No timestamps are fabricated in the runner as proof of preregistration.

NumPy, Pandas, SciPy, scikit-learn, NetworkX and Pytest are used. PyMC is intentionally not a dependency: the proposed toy linear-Gaussian simulation has fixed parameters and direct sampling, so MCMC would add complexity without a Bayesian posterior to estimate. Use PyMC only in a future, explicitly specified and checked parameter-inference study.

## A. Temporal Financial State Fusion (TFSF)

### Mathematical specification

Let the monthly investor state be \(x_t=(I_t,E_t,A_t,L_t,G_t)^T\): income and expense flows in currency/month; assets, liabilities and a monetary goal target in currency. This implementation is a **diagonal random walk**, not a full balance-sheet accounting model:

\[
x_t=x_{t-1}+w_t,\quad w_{t,j}\sim N(0,q\Delta t),\qquad y_{t,j}=x_{t,j}+v_{t,j},\quad v_{t,j}\sim N(0,r_{t,j}).
\]

Each dimension has an independent Gaussian prior \(N(m_0,P_0)\). For observed dimension \(j\), \(P^-_{t,j}=P_{t-1,j}+q\Delta t\), \(K=P^-/(P^-+r)\), \(m_t=m_{t-1}+K(y-m_{t-1})\), and \(P_t=(1-K)P^-\). For missing dimensions, carry \(m\) forward and use \(P^-\); missing is not an observation of zero. Mark whether each dimension has ever been observed. The prior mean (default 0) is an explicit placeholder prior, **not a verified financial balance**. A two-sided innovation threshold \(|y-m^-|/\sqrt{P^-+r}\ge z\) flags a possible change only after the first observation. This heuristic is not a formal change-point detector or calibrated significance test. Independent dimensions ignore correlations and balance-sheet identities; no constraints enforce nonnegative income or balances.

### Input/output schema

- `Observation(timestamp: str, values: Mapping[str, float | None], variances: Mapping[str, float], currency: str = "USD")`, passed chronologically to `fuse`. Timestamps are timezone-aware ISO 8601; strictly increasing with one batch per timestamp. Supported keys: `income`, `expenses`, `assets`, `liabilities`, `goal`. Each present finite value requires a positive finite observation variance in squared units. Missing keys or `None` are missing measurements. All observations in a series must share one uppercase three-letter currency; no conversion is performed. Inputs must already use consistent monthly flow units. The API does not validate that currency codes are genuine ISO codes, does not handle individual goal identities, and does not establish a true available-at timestamp separately from the supplied timestamp.
- `Config(process_variance=25, prior_mean=0, prior_variance=1000000, change_z=3, use_observation_uncertainty=True, detect_changes=True)`; process variance is per elapsed day and shared across dimensions. Choose priors, process and noise variances using training/validation data in real research, not test outcomes.
- Output is a tuple of frozen `Snapshot(timestamp, currency, mean, variance, observed, initialized, changes)` in `DIMENSIONS` order. It is an in-memory historical snapshot sequence, not an immutable database or secure audit ledger. `observed` indicates this batch; `initialized` means at least one observation has occurred. Model posterior variance is not guaranteed to cover the truth under misspecification.

**Baseline:** last observation carried forward with the same initial prior; its carried variance is a descriptive placeholder, not a calibrated baseline interval. **Ablations:** `ignore_uncertainty` replaces all measurement variances with 1; `no_change_detection` suppresses flags, leaving estimates unchanged. **Benchmark:** synthetic random-walk truth, noise levels, 20% missing values and an income jump halfway; compare model and baseline MAE overall and per dimension, model nominal 90% coverage, and detection exactly at the injected break. Record every observation, latent truth, model and baseline snapshot. Overall MAE mixes economically different scales; per-dimension metrics are essential. A single-run jump flag does not measure change detection sensitivity or false-positive rate; the break may coincide with a missing observation. More seeds, regime and missingness sensitivity, and an independent holdout are needed.

## B. Evidence-Constrained Counterfactual Reasoning (ECCR)

### Mathematical specification

The implemented model is a static, acyclic, linear Gaussian SCM. For a NetworkX DAG and topological order,

\[
V_j=b_j+\sum_{k\in pa(j)}\beta_{jk}V_k+\epsilon_j,\qquad \epsilon_j\sim N(0,\sigma_j^2),
\]

with independent errors **assumed**, not discovered. For `do(V_m=a)`, replace the structural equation for node \(m\) by \(V_m=a\), leaving descendant equations unchanged. Draw independent error vectors with a seeded NumPy generator. Baseline and intervened paths use the **same** draws: \(\Delta_j^{(s)}=V_j^{do(a),(s)}-V_j^{base,(s)}\). Return samples, mean, 5–95% quantiles and paired-delta samples. These are forward, model-conditional scenario distributions; they are not individual retrospective counterfactuals because observed history is **not used to abduct exogenous variables**. Observations are retained as provenance metadata and do not condition or fit the model.

### Input/output schema

`Evidence(id, source, available_at)` supplies an identifier, source pointer and timezone-aware availability. `Observation(variable, value, unit, evidence_id)` records a separately sourced fact. `Node(name, unit, intercept, noise_std, parents: {name: coefficient}, assumption_evidence: tuple[id, ...], allowed_range: (low, high) | None)` specifies an equation, linked assumption sources and optional declared intervention range. `Scenario(as_of, nodes, evidence, observations, intervention: {name: Intervention(value, unit)}, assumptions_approved, no_unmeasured_confounding_assumed)` separates the observed facts from model assumptions. `Config(draws, seed, enforce_approval, enforce_support, structural_edges)` controls simulation and ablations. Evidence IDs must exist and be public by `as_of`; source pointers are metadata, not verified document content or proof of a causal relationship. Independent errors and the confounding flag are assertions.

The validator rejects duplicate/missing names or evidence, future evidence, invalid units, nonfinite parameters, unknown parents, directed cycles, missing approvals, unapproved confounding assumptions and interventions outside a declared range. A declared range is a **policy constraint**, not an empirically established conditional-positivity test. It cannot discover omitted confounding or assess source quality; in particular, approval flags are not proof of identification. With safeguards disabled, output is explicitly `model_conditional_hypothetical`. Structural errors are never disabled.

Output: `status` (`OK` or `ABSTAIN`), `errors`/`warnings`, `interpretation`, graph order, seed/draw count, evidence and assumption IDs, observed facts, and per-node units, samples, quantiles and paired contrasts. These are not identified effects or confidence/credible intervals and are not personalized recommendations. Counterfactual, do-query and posterior inference require additional assumptions and work beyond this prototype.

**Baseline:** same nodes and noise, but ignores all parent edges; intervention consequences for other variables are then absent. It is an intentionally simple misspecified baseline, not an established causal method. **Ablations:** `independent_nodes`, `no_support_check`, `no_approval_check` (last two retain warnings even when data would pass). **Benchmark:** synthetic income/expense/savings equations with known coefficient 1, compare the estimated mean paired savings change under income intervention with the analytic contrast 500, and compare independent-node baseline. Its factual income mean happens to equal the reference action 3000; consequently the paired contrast equals `E[savings | do(income=3500)] - E[savings | do(income=3000)]` for this example. Report Monte Carlo error, not proof the model or identification assumptions are valid. Expand to held-out SCMs, misspecification, confounding, support and source errors before H2 evaluation.

## C. Disagreement-Preserving Research Optimization (DPRO)

### Mathematical specification

Three fixed deterministic analyst-role heuristics read the same frozen evidence independently of one another. For available fractional revenue growth \(g\) and debt ratio \(d\), role \(k\) emits \(p_k=\sigma(w_{kg}g+w_{kd}d+b_k)\) for a user-specified binary event. Coefficients are implementation constants, not fitted or calibrated probabilities. A missing metric contributes no signal and is not represented as a factual zero. The report uses equal-weight probability \(\bar p=K^{-1}\sum p_k\), hard-vote consensus fraction \(\max(n_+,K-n_+)/K\), and average pairwise Jensen–Shannon **divergence** in bits between Bernoulli distributions \((p_k,1-p_k)\). SciPy's Jensen–Shannon distance is squared to obtain divergence. No claim is made that high divergence means accuracy, or that shared-data roles are independent experts. This prototype does not solve an optimization objective: “optimization” is the project label for a future empirical research question.

### Input/output schema

`Evidence(id, source, metric, period, value, unit, available_at)` is a finite numeric observation with source and timezone-aware release time. Input evidence IDs are unique, timestamps must not be later than `as_of`, and all input evidence must have the same period. Recognized signals are `revenue_growth` and `debt_ratio`, both in `fraction` units. If multiple same-metric records exist, the most recently available, breaking ties by ID, is used while all conflicting records remain reported. Same metric/period/unit values farther apart than the configured absolute tolerance are flagged as contradictory; they may represent revisions or different definitions rather than actual errors.

`generate_hypotheses(evidence, as_of, config)` returns a tuple of frozen `Hypothesis(agent, probability, conclusion, claims)`, each claim carrying metric, period, value, unit and evidence ID. `report(hypotheses, evidence, references, as_of, config)` returns the original conclusions, visible conclusions, consensus probability/vote fraction, divergence, contradiction pairs, claim cross-checks and caveats. Original conclusions are retained even when the presentation ablation hides dissent. A claim is independently cross-checked against a separate caller-supplied reference collection whose matching source string must differ from the cited source. Status is `SUPPORTED_BY_REFERENCE`, `CONTRADICTED_BY_REFERENCE`, `UNVERIFIABLE`, or `INVALID_CITATION`. A different source name is only a necessary check; common provenance, fabricated references, semantic entailment, restatements and actual source independence require external auditing. Verification is a numeric comparison, not proof of truth or a general financial fact checker. Records are in-memory/JSON, not tamper-proof or append-only storage.

**Baseline:** hard majority-vote 0/1 probability on the same heuristics. Its Brier score is intentionally a simple, potentially overconfident baseline; it is not a substitute for strong, matched probabilistic ensembles or literature baselines. **Ablations:** `hide_dissent` suppresses the presentation list but retains the immutable originals in the report; `exclude_conflicts` removes conflicting metrics before generation, abstaining if none remain. **Benchmark:** synthetic input signals, simulated binary outcomes, optional injected inconsistent growth values and an independent synthetic reference; report Brier scores of the soft ensemble and hard-vote baseline on answered cases, injected-conflict detection, answer counts and all per-case inputs/results. Outcomes and synthetic reference correctness are generated by construction. This is not evidence of real-world source verification, calibration, agent autonomy or improved forecasting.

## Testing, limits, and next research gates

Pytest covers each algorithm's unit behavior and an end-to-end handoff, malformed data/causal checks, seed replay, ablation paths and CLI records. The end-to-end handoff verifies provenance plumbing only; ECCR explicitly does not condition on the TFSF estimate. `experiments/records/` are illustrative records, not preregistered experiments. For real evaluations, first complete the search, preregistration, data-source rights and availability audit, power analysis, baseline selection, and time-split/contamination controls described in the research design. Add multiple seeds, uncertainty on metrics, sensitivity and ablations, stronger literature baselines and independent replications. Secure, versioned data persistence, adversarial data handling, model governance, security review and privacy controls are not implemented. No real investor data, broker connection, live execution or investment recommendation is provided.
