---
name: a-share-research
description: Unified entry for the workspace-bound A-share research system. Use for natural-language investment requests, ambiguous research intent, premarket or intraday outlooks, event analysis, compound review-then-outlook tasks, and any request that needs routing across opportunity scanning, evidence investigation, judgment analysis, dual-axis review, or weekly meta-review.
---

# A-share research router

Route work; do not create facts, judgments, reviews, lessons, or parameters yourself.

The shared context module is the only context-construction boundary. Use
`../shared/context/` through `../shared/scripts/context_workspace.py`; do not
read the projection database, parse Markdown, or call a semantic adapter from
this router.

## Preflight

1. Locate the ancestor containing `CONTEXT.md`, `研究规则.md`, `经验库.md`, and `对象档案/索引.md`. This is the only writable research workspace.
2. Run `python3 ../shared/scripts/validate_workspace.py --root <workspace>` from this skill directory. Stop persistent writes on errors; disclose warnings.
3. Create a versioned run manifest with workflow, stage, object, information cutoff and task contract reference. Instantiate the task evidence list; the model may add conditions but cannot remove contract requirements.
4. Run `context_workspace.py assemble` and inspect coverage, gaps, projection freshness and adapter degradation. Use `hydrate` for only the critical stable references; keep full source payloads outside the phase context.
5. Report in two or three lines: active judgment count, latest lesson-state change or evidence cluster, items due today, active observation count, relevant dossier staleness, and workset coverage/degradation.
6. Save a run record and workset manifest only if a selected workflow makes a persistent write.

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

Pass the same run ID, task contract version and manifest between workflows. Allocate `RUN-YYYYMMDD-NNN` with `../shared/scripts/next_id.py RUN --root <workspace> --date YYYYMMDD` when the first persistent write becomes necessary. Preserve separate information snapshots and phase worksets. Reuse canonical artifacts by ID; never paste one workflow's facts into a different canonical format.

## Finish

Return the route, canonical artifact IDs, report paths, workset manifest path, combined conclusion, unknowns, and any workflow not executed. Reports need a stable path, not an invented artifact ID. Never hide a specialist's abstention, coverage gap, projection degradation or validation failure. Reports must state “不构成投资建议”.
