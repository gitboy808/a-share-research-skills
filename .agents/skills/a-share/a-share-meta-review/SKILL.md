---
name: a-share-meta-review
description: Run weekly governance for the A-share research system: segmented calibration, lesson lifecycle changes, scan quality, candidate-strategy evaluation, bounded L0-L2 autonomy, rollback, and L3 proposals. Use explicitly for weekly convergence or system optimization, not daily review.
---

# A-share meta-review

Improve the system without rewriting its history or weakening risk guardrails.

Read the workspace-root `研究规则.md` as the sole normative rule source and
`CONTEXT.md` for domain language. This skill defines meta-review execution only.

Use a frozen meta-review workset assembled by `../shared/context/`. The
versioned contract supplies the review window, current v3 samples, strategy
versions, evidence clusters and prior workset manifests. Semantic candidates
can expand reading only after stable-reference hydration; they cannot replace
the required coverage.

Follow the [阶段运行协议](../shared/contracts/README.md#阶段运行协议) for every routed or direct run.

## Start

From a router, reuse only its validated workspace and RUN ID. When called directly, validate the workspace and allocate a RUN ID before any persistent write. In both cases, create a fresh phase manifest with `workflow: meta-review`, `stage: meta-review`, canonical review-window objects, a timezone-aware `calibration_window_start`, a timezone-aware `information_cutoff`, the versioned meta-review task contract, and an independent workset-manifest target. Assemble it, then load the review window's judgments and reviews, `经验库.md`, `策略库/索引.md`, observation logs, data incidents, prior convergence report, and workset quality fields from stable references. Freeze both window boundaries.

The registered contract uses `calibration_window`: concluded judgments and retired/limited historical strategy or lesson samples may be inspected inside the window, but their inclusion here never makes them eligible for current analysis. Do not copy calibration samples into a prospective workset.

## Workflow

1. Segment current v3 samples by outcome, process, confidence band, horizon, market state, driver, pricing mode, and strategy profile. Do not rely on overall hit rate.
2. Evaluate abstention quality and scan discovery rate, lead time, false positives, and missed-opportunity counterfactuals.
3. Cluster repeated errors by independent catalyst/market episode. Process-distorted samples cannot calibrate probabilities.
4. Apply lesson lifecycle mechanically: observation at one independent cluster; candidate at two to four; validated only at five or more across at least two market states with dual-axis quality; restrict or retire on boundary-breaking counterexamples.
5. Compare official and candidate strategies on identical information snapshots. Apply the promotion priority: process integrity → risk errors → calibration → judgment quality → opportunity efficiency. A later improvement cannot offset an earlier deterioration.
6. L0 records statistics and evidence. L1 changes lesson status under fixed gates. L2 may create, promote, restrict, or roll back only authorized strategy fields.
7. Structural parameters require at least ten independent evidence clusters, two market states, and four natural weeks by default. Situational parameters may activate immediately only inside declared scope, horizon, and expiry, and may not lower evidence floors.
8. Every candidate-strategy experiment preregisters scope, primary metric, protected metrics, sample definition, stop condition, and rollback value. Never redefine them after observing results.
9. Risk principles, evidence floors, abstention, dual-axis review, output boundary, error taxonomy, skill architecture, and field permissions are L3. Propose changes with evidence; do not apply without user confirmation and an ADR when appropriate.
10. Compact resident files within limits, refresh indexes, and retain all immutable histories.

## Strategy storage

Use `模板/策略版本模板.md`. Strategy versions are Markdown with YAML metadata under `策略库/`; states are trial, candidate, official, limited, or retired. Trial values are design priors, not validated edge.

## Output and writes

May update lesson states, strategy versions, calibration statistics, resident indexes, and archives within L0–L2. Must not modify old evidence packages or judgment snapshots.

## Close research, then present

After lesson, strategy, calibration, and index writes pass workspace validation, confirm that `hydrate` updated this meta-review phase's independently persisted workset manifest and emit its phase closure record. End the research context and release source-payload text and handles, verification excerpts, tool history, and temporary reasoning from the active context; never delete a store entry still referenced by canonical evidence.

Start a new presentation context with `模板/元复盘模板.md`, the closure record, and only canonical artifact IDs and stable references. It may write `周收敛/YYYY-Www.md` without recomputing samples, changing gates, promoting parameters, or introducing new L3 decisions. A presentation failure never rewrites validated lesson or strategy versions. Reports state “不构成投资建议”.
