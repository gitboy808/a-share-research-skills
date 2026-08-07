---
name: a-share-meta-review
description: Run weekly governance for the A-share research system: segmented calibration, lesson lifecycle changes, scan quality, shadow strategy evaluation, bounded L0-L2 autonomy, rollback, and L3 proposals. Use explicitly for weekly convergence or system optimization, not daily review.
---

# A-share meta-review

Improve the system without rewriting its history or weakening risk guardrails.

Use a frozen meta-review workset assembled by `../shared/context/`. The
versioned contract supplies the review window, legacy/current strata, strategy
versions, evidence clusters and prior workset manifests. Semantic candidates
can expand reading only after stable-reference hydration; they cannot replace
the required coverage.

## Start

If no reusable router manifest exists, perform the preflight in `../a-share-research/SKILL.md`. Assemble the meta-review contract, then load `模板/元复盘模板.md`, the review window's judgments and reviews, `经验库.md`, `策略库/索引.md`, observation logs, data incidents, prior convergence report and workset quality fields. Freeze the meta-review cutoff.

## Workflow

1. Separate legacy and current-schema samples. Segment by outcome, process, confidence band, horizon, market state, driver, pricing mode, and strategy profile. Do not rely on overall hit rate.
2. Evaluate abstention quality and scan discovery rate, lead time, false positives, and missed-opportunity counterfactuals.
3. Cluster repeated errors by independent catalyst/market episode. Process-distorted samples cannot calibrate probabilities.
4. Apply lesson lifecycle mechanically: observation at one independent cluster; candidate at two to four; validated only at five or more across at least two market states with dual-axis quality; restrict or retire on boundary-breaking counterexamples.
5. Compare official and shadow strategies on identical information snapshots. Apply the promotion priority: process integrity → risk errors → calibration → judgment quality → opportunity efficiency. A later improvement cannot offset an earlier deterioration.
6. L0 records statistics and evidence. L1 changes lesson status under fixed gates. L2 may create, promote, restrict, or roll back only authorized strategy fields.
7. Structural parameters require at least ten independent evidence clusters, two market states, and four natural weeks by default. Situational parameters may activate immediately only inside declared scope, horizon, and expiry, and may not lower evidence floors.
8. Every shadow experiment preregisters scope, primary metric, protected metrics, sample definition, stop condition, and rollback value. Never redefine them after observing results.
9. Risk principles, evidence floors, abstention, dual-axis review, output boundary, error taxonomy, skill architecture, and field permissions are L3. Propose changes with evidence; do not apply without user confirmation and an ADR when appropriate.
10. Compact resident files within limits, refresh indexes, and retain all immutable histories.

## Strategy storage

Use `模板/策略版本模板.md`. Strategy versions are Markdown with YAML metadata under `策略库/`; states are trial, shadow, official, limited, or retired. Trial values are design priors, not validated edge.

## Output and writes

Use `模板/元复盘模板.md` and write `周收敛/YYYY-Www.md`. May update lesson states, strategy versions, calibration statistics, resident indexes, and archives within L0–L2. Must not modify old evidence packages or judgment snapshots. Reports state “不构成投资建议”.
