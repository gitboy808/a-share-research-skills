---
name: a-share-review
description: Verify A-share judgments with exact market data on separate outcome and decision-process axes, including abstention counterfactuals, error attribution, and evidence-cluster updates. Use explicitly for due, falsified, denied, or data-polluted judgments; never for a new outlook.
---

# A-share dual-axis review

Judge the old decision using its original snapshot. Do not use later facts to improve its process score.

Review gets a separate phase workset from `../shared/context/`. Its task
contract must preserve the original snapshot, evidence and strategy stable
references; hydrate later market data only for the declared outcome window.
Do not reuse the prior investigation tool history or stale projection text.

## Start

If no reusable router manifest exists, perform the preflight in `../a-share-research/SKILL.md`. Assemble the review contract, then load `模板/盘后复盘模板.md`, due-item views, the canonical monthly log, original evidence package, strategy version and linked dossier/report from stable references.

Review at expiry. Before expiry, close only when a predeclared falsifier, official denial, or data/process pollution has occurred. Ordinary price movement or weaker conviction requires a new analysis version, not early scoring.

## Verification

1. Freeze the review cutoff and exact market-data definition. Verify open/high/low/close, relative benchmark, trigger order, corporate actions, timezone, and adjustment method with authoritative or independent sources. Preserve unresolved conflicts.
2. Reconstruct what was known at the original cutoff. Exclude future information from process evaluation.
3. Assign one outcome state only: ongoing, untriggered, realized, falsified, or indeterminate. Never use partial correctness to merge propositions.
4. Assign one process state: compliant, defective, or distorted. Distorted samples do not enter calibration.
5. For abstention, apply only the predeclared counterfactual: confirmation had to occur, then volatility-adjusted significant relative performance had to occur without prior invalidation. Random jumps do not punish abstention.
6. When evidence identifies a defect, choose one primary error and optional secondary error: data/definition, source, regime, transmission, weighting, horizon/timing, threshold calibration, or exogenous shock. A falsified probabilistic judgment with a compliant process and no identifiable defect uses `不适用—合规概率损失`; it enters aggregate calibration but does not manufacture a lesson from one sample.
7. Audit original driver, pricing mode, strategy profile, price-discipline gate, chase error, wrong exit, and narrative attribution.
8. Group the same catalyst/market move into one evidence cluster. Append support, counterexample, or pause evidence; do not change lesson status or strategy parameters. A single `不适用—合规概率损失` may enter its calibration cohort but must not create lesson-support or counterexample evidence.
9. Append result and process to the canonical log. Remove concluded items from `当前判断.md`; never edit their original text. List factual dossier fields requiring later investigation.

For legacy judgments, audit against the rules and fields required at their original cutoff. Missing v3-only fields are `legacy audit scope limited`, not automatically distorted; never backfill them. Keep legacy calibration separate from v3.

## Output

Use `模板/盘后复盘模板.md`. If the user also requests an outlook, finish and freeze review first, then return control to the router for a new investigation and analysis snapshot. Reports state “不构成投资建议”.

## Write boundary

May append outcome/process/error/evidence-cluster records, remove concluded current mirrors, and write review reports. Must not rewrite original judgments, promote lessons, adjust parameters, or form a new outlook.
