---
name: a-share-review
description: Verify A-share judgments with exact market data on separate outcome and decision-process axes, including abstention counterfactuals, error attribution, and evidence-cluster updates. Use explicitly for due, falsified, denied, or data-polluted judgments; never for a new outlook.
---

# A-share dual-axis review

Judge the old decision using its original snapshot. Do not use later facts to improve its process score.

Read the workspace-root `研究规则.md` as the sole normative rule source and
`CONTEXT.md` for domain language. This skill defines review execution only.

Review gets a separate phase workset from `../shared/context/`. Its task
contract must preserve the original snapshot, evidence and strategy stable
references; hydrate later market data only for the declared outcome window.
Do not reuse the prior investigation tool history or stale projection text.

Follow the [阶段运行协议](../shared/contracts/README.md#阶段运行协议) for every routed or direct run.

## Start

From a router, reuse only its validated workspace and RUN ID. When called directly, validate the workspace and allocate a RUN ID before any persistent write. In both cases, create a fresh phase manifest with `workflow: review`, `stage: review`, canonical objects, a timezone-aware `information_cutoff`, the versioned review task contract, `handoff.judgment_ids`, `handoff.evidence_ids`, and an independent workset-manifest target. Assemble it, then load due-item views, the canonical monthly log, original evidence and strategy versions, and linked dossier fields only from its stable references.

The registered contract compiles three `historical_as_of` boundaries: the original judgment uses its unit snapshot, process evidence uses the original judgment cutoff, and outcome evidence uses the review cutoff. Evidence expiring after the original judgment does not invalidate reconstruction; evidence created after it cannot improve the process score. Do not collapse these boundaries into a review-wide freshness rule.

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
9. Append result, a strict timezone-aware result-recorded timestamp, and process to the canonical log. Remove concluded items from `当前判断.md`; never edit their original text. List factual dossier fields requiring later investigation. The context module derives current eligibility from the append-only result and horizon.

For every current v3 judgment, preserve the fields, unknowns, and gaps recorded at its original cutoff. Never backfill the original snapshot or treat an explicitly limited audit scope as automatically distorted.

## Output

If the user also requests an outlook, finish and freeze review first, then return control to the router. The outlook starts a new investigate phase and then a new analyze phase, each with its own manifest, contract, cutoff, workset, and closure record.

## Write boundary

The research context may append outcome/process/error/evidence-cluster records and remove concluded current mirrors; the later presentation context may write review reports. Neither may rewrite original judgments, promote lessons, adjust parameters, or form a new outlook.

## Close research, then present

After review-log, evidence-cluster, and current-view writes pass workspace validation, confirm that `hydrate` updated this review phase's independently persisted workset manifest and emit its phase closure record. End the research context and release later-data tool history, source-payload text and handles, verification excerpts, and temporary reasoning from the active context; never delete a store entry still referenced by canonical evidence.

Start a new presentation context with `模板/盘后复盘模板.md`, the closure record, and only canonical artifact IDs and stable references. Allocate its report ID with `../shared/scripts/next_id.py RPT --root <workspace> --date YYYYMMDD` before writing `报告/YYYY-MM/RPT-YYYYMMDD-NNN.md`. It may render the recorded result and process axes without searching, changing either state, backfilling the original snapshot, or forming an outlook. A presentation failure never changes the validated review record. Reports state “不构成投资建议”.
