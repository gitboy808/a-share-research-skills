---
name: a-share-research
description: Unified entry for the workspace-bound A-share research system. Use for natural-language investment requests, ambiguous research intent, premarket or intraday outlooks, event analysis, compound review-then-outlook tasks, and any request that needs routing across opportunity scanning, evidence investigation, judgment analysis, dual-axis review, or weekly meta-review.
---

# A-share research router

Route work; do not create facts, judgments, reviews, lessons, or parameters yourself.

## Preflight

1. Locate the ancestor containing `CONTEXT.md`, `研究规则.md`, `经验库.md`, and `对象档案/索引.md`. This is the only writable research workspace.
2. Run `python3 ../shared/scripts/validate_workspace.py --root <workspace>` from this skill directory. Stop persistent writes on errors; disclose warnings.
3. Read `CONTEXT.md`, `研究规则.md`, `经验库.md`, `当前判断.md`, `观察池.md`, `策略库/索引.md`, and `对象档案/索引.md`, then only the relevant dossiers and linked history.
4. Report in two or three lines: active judgment count, latest lesson-state change or evidence cluster, items due today, active observation count, and relevant dossier staleness.
5. Create an in-memory run manifest. Save it with `模板/运行记录模板.md` only if a selected workflow makes a persistent write.

## Route

| Intent | Workflow |
|---|---|
| “看看、研究一下、了解一下” | investigate only |
| verify facts, business, industry-chain membership | investigate |
| scan, rotation, catch-up, low-heat opportunity | scan |
| analyze/assess a named object, direction, bull/bear logic, sustainability, tracking metrics | investigate → analyze |
| premarket | scan refresh → investigate → analyze(premarket) |
| auction, intraday, event impact | investigate(delta) → analyze(stage) |
| review or verify an old judgment | review |
| weekly convergence, calibration, optimize strategies | meta-review |
| review then outlook | review and freeze → new investigate snapshot → new analyze |

Low-confidence routing defaults to investigation, not prediction. A single-object request checks direct substitutes and rotation mappings but does not run a full-market scan unless asked.

## Compose

Read the selected sibling `SKILL.md` completely before running it:

- `../a-share-scan/SKILL.md`
- `../a-share-investigate/SKILL.md`
- `../a-share-analyze/SKILL.md`
- `../a-share-review/SKILL.md`
- `../a-share-meta-review/SKILL.md`

Pass the same run ID and manifest between workflows. Allocate `RUN-YYYYMMDD-NNN` with `../shared/scripts/next_id.py RUN --root <workspace> --date YYYYMMDD` when the first persistent write becomes necessary. Preserve separate information snapshots. Reuse canonical artifacts by ID; never paste one workflow's facts into a different canonical format.

## Finish

Return the route, canonical artifact IDs, report paths, combined conclusion, unknowns, and any workflow not executed. Reports need a stable path, not an invented artifact ID. Never hide a specialist's abstention or validation failure. Reports must state “不构成投资建议”.
