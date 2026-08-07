---
name: a-share-analyze
description: Form risk-first A-share market, industry, theme, or stock analyses from valid evidence packages, including dynamic strategy selection, bull and bear logic, price-discipline gates, tracking indicators, abstention, and falsifiable judgments. Use explicitly for directional analysis, not fact gathering.
---

# A-share judgment analysis

Consume evidence; form auditable conditional judgments. Never perform unrecorded investigation.

Consume only a phase workset assembled by `../shared/context/` from the
analysis task contract and a formally handed-off evidence package. Hydrate
critical stable references before use. Do not parse the projection or source
payloads, and return an explicit increment-investigation request when the
assembled coverage is insufficient.

## Start

If no reusable router manifest exists, perform the preflight in `../a-share-research/SKILL.md`. Use the assembled workset to load `模板/分析报告模板.md`, `模板/判断条目模板.md`, relevant dossier views, judgment-chain references, strategy version and cited evidence package.

Validate task-required evidence item by item. If no evidence package exists, return a non-persistent `investigation_required` workflow block with `research_object`, `snapshot_cutoff`, `required_evidence`, `missing_evidence`, and `next_skill: a-share-investigate`; write no judgment. If a cited package exists but critical evidence is absent, conflicting, or expired, return control to the router to request an explicit evidence refresh. If that refresh is not run or cannot close the critical gaps, issue a scored abstention that cites the existing package and records gaps, confirmation signal, horizon, and counterfactual; do not silently investigate.

## Workflow

1. Freeze analysis stage and information snapshot: premarket, auction, intraday, event, or non-trading专题.
2. State the delta from the prior dossier and judgment chain.
3. Classify the four-dimensional market state. Adverse market state raises downstream evidence requirements; favorable state never lowers the stock floor.
4. Evaluate overseas baseline and only object-relevant transmission mappings.
5. Classify industry/theme state and decompose stock performance into market beta, industry/theme beta, and idiosyncratic relative performance.
6. Select one primary market driver and at most one secondary driver. State confirmation, falsification, and expected half-life. If no primary driver clears the evidence threshold, mark mixed/unknown.
7. Select pricing mode: fundamental, sentiment, mixed, or unknown. For event news, process fact status → expectation gap → priced-in state → price acceptance. A true positive headline may still imply downside when fully priced.
8. Select one base strategy profile and at most one expiring tactical modifier from the cited strategy version. Declare evidence roles: veto, primary, confirmation, and background. Veto evidence cannot be offset by a total score.
9. Write separate falsifiable bull and bear causal chains. Choose exactly one base scenario only when one side clears the evidence floor and reaches 55%; otherwise abstain.
10. Apply the independent price-discipline gate: acceptable displacement, confirmation window, falsification distance, remaining potential, crowding, breadth, leadership, and price acceptance. A valid thesis with lost odds becomes wait/avoid, never a chase.
11. Output one of: abstain, avoid, wait for confirmation, research condition met, or holding thesis invalid. Use wait when the causal thesis remains valid, no veto has fired, and a bounded confirmation trigger remains inside the declared horizon. Use avoid when a veto has fired, price acceptance has failed under crowding/decay, or downside cannot be bounded without a new evidence package. Personalized holding discipline requires holding state, cost range, horizon, original thesis, and invalidation; otherwise provide public verification lines only.
12. Create atomic judgments and chains. Each judgment cites evidence package/item IDs and strategy version, includes one horizon, probability band, falsification, expiry, and no more than six leading/confirmation/falsification indicators.

## Constraints

- Confidence bands: 55–59%, 60–69%, 70–79%, ≥80%; below 55% abstains.
- A new unvalidated situational parameter can run only at low confidence. A trial parameter limits confidence only when the conclusion is sensitive to it.
- Treat “main-force intent” and deliberate narrative amplification only as competing, falsifiable hypotheses with non-manipulative alternatives.
- Do not issue buy/sell instructions or position sizes. Every report states “不构成投资建议”.

## Output and writes

Use the analysis and judgment templates. Judgment IDs use `JYYYYMMDD-NNN`; obtain the next ID with `../shared/scripts/next_id.py J --root <workspace> --date YYYYMMDD`. Append to `判断日志/YYYY-MM.md`, mirror active items to `当前判断.md`, update analytical dossier fields by delta, and write the report. Never rewrite evidence, review old judgments, or upgrade lessons.
