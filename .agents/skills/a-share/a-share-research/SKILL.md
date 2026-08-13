---
name: a-share-research
description: Unified entry for the workspace-bound A-share research system. Use for natural-language investment requests, ambiguous research intent, premarket or intraday outlooks, event analysis, compound review-then-outlook tasks, and any request that needs routing across opportunity scanning, evidence investigation, judgment analysis, dual-axis review, or weekly meta-review.
---

# A-share research router

Route work; do not create facts, judgments, reviews, lessons, or parameters yourself.

Read the workspace-root `研究规则.md` as the sole normative rule source and
`CONTEXT.md` for domain language. This skill defines routing and stage execution only.

The shared context module is the only context-construction boundary. Use
`../shared/context/` through `../shared/scripts/context_workspace.py`; do not
read the projection database, parse Markdown, or call a semantic adapter from
this router.

## Preflight

1. Locate the ancestor containing `CONTEXT.md`, `研究规则.md`, `经验库.md`, and `对象档案/索引.md`. This is the only writable research workspace.
2. Run `python3 ../shared/scripts/validate_workspace.py --root <workspace>` from this skill directory. Stop persistent writes on errors; disclose warnings.
3. Classify the request into the workflow sequence below. Low-confidence intent stops at investigation.
4. Read and apply the [阶段运行协议](../shared/contracts/README.md#阶段运行协议). Do not create a cross-workflow manifest.
5. Allocate `RUN-YYYYMMDD-NNN` with `../shared/scripts/next_id.py RUN --root <workspace> --date YYYYMMDD` when the first persistent workflow becomes necessary.

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

Reuse only the RUN ID across workflow phases. For every selected workflow:

1. Start a fresh phase context and create a fresh phase manifest with that workflow's `workflow`, `stage`, canonical objects, timezone-aware `information_cutoff`, versioned `task_contract`, handoff IDs, and independent workset-manifest target. A prior manifest, contract, task evidence list, cutoff, or workset is not reusable.
2. Run `assemble`; inspect every required item, blocking gap, projection state, and adapter degradation. Run `hydrate` only for critical stable references.
   Eligibility mode comes only from the registered contract. Never hydrate `audit_references` or reimplement expiry, lifecycle, or supersession filtering in the router.
3. Let the specialist write only its canonical artifacts. Validate schema, IDs, references, version chains, and workspace health before accepting the write.
4. Confirm that `hydrate` updated that phase's independently persisted workset manifest, emit the phase closure record defined by the protocol, and end the phase context. Release source-payload text and handles from the active context, together with tool history, verification excerpts, and temporary reasoning; never delete a store entry still referenced by canonical evidence.
5. Start the next workflow from the closure record. Pass formal artifact IDs and stable references, using `handoff.evidence_ids` for analysis and both `handoff.judgment_ids` and `handoff.evidence_ids` for review.

An analysis coverage gap returns an incremental-investigation request. Run it with a new investigate manifest and cutoff, then create another new analyze manifest and reassemble; never resume the old analysis context.

## Finish

After every canonical write is validated and its research context has ended, start a new presentation context. Give it only the relevant template, phase closure records, canonical artifact IDs and stable references. It may compose reports and the user-facing answer without searching, adding facts, changing judgments, or adjusting confidence.

Return the route, phase-by-phase canonical artifact IDs, report paths, workset manifest paths, combined conclusion, unknowns, and any workflow not executed. Reports need a stable path, not an invented artifact ID. Never hide a specialist's abstention, coverage gap, projection degradation or validation failure. Reports must state “不构成投资建议”.
