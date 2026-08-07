---
name: a-share-investigate
description: Verify current A-share market, industry-chain, trading-theme, company, policy, and cross-market facts and create auditable immutable evidence packages. Use explicitly for factual research without forcing a directional judgment.
---

# A-share evidence investigation

Produce verified evidence; do not predict.

Use the deep module in `../shared/context/` as the only workset boundary.
Instantiate the investigate contract, assemble required coverage, and hydrate
critical source locations. Raw web/PDF/API payloads belong in the external
source-payload store and must not be copied into the workset or evidence
package.

## Start

If no reusable router manifest exists, perform the preflight in `../a-share-research/SKILL.md`. Use the assembled workset to load `模板/调研报告模板.md`, `模板/证据包模板.md`, relevant dossier views, and cited evidence units. Freeze cutoff time, timezone, market dates, and data definitions.

## Source discipline

- Search dynamically to avoid an information cocoon. Prefer original filings, exchange and regulator material, policy text, official statistics, issuer disclosures, and primary overseas sources; use high-quality financial reporting for context.
- Multiple reprints of one source count once. Preserve conflicts and lower completeness. Filter source-less predictions, SEO farms, AI aggregation, and unverifiable claims.
- Community, search, and media-frequency signals may measure narrative heat only. They cannot establish business facts or causality.
- Separate publication time from event time. Match timezone, units, adjustment method, and trading date before comparing values.

## Parallel research

For genuinely independent directions, use subagents for overseas markets, industry/policy, company filings, market structure, or counterevidence. Give each a non-overlapping question. Subagents return candidate atomic evidence and sources only; they must not write workspace files or form the official conclusion. The main agent reopens critical sources, deduplicates provenance, reconciles definitions, and performs the only canonical write.

## Workflow

1. Resolve object type: market, industry chain, trading theme, stock, or event. Load only the relevant object checklist from the workspace templates and domain model.
2. Compare with the previous dossier and evidence package. Report additions, strengthening, weakening, denial, maintenance, and expired fields.
3. Write one verifiable claim per atomic evidence item. Record status, independent source chain, event/publication time, market date, definition, related object/field, and expiry condition.
4. Separate confirmed facts, narrative signals, competing hypotheses, conflicts, denials, and missing evidence.
5. For stocks, verify E0–E4 realization level with dated primary evidence. For cross-market links, declare transmission tier, direction, lag, and validity window.
6. Mark completion as complete, partial, or unavailable. This status never becomes a directional conclusion.
7. Save a new immutable evidence package only when facts changed, a dossier was refreshed, or the package will enter a judgment chain. Otherwise return a read-only research summary.

## Output

Use `模板/证据包模板.md` and `模板/调研报告模板.md`. IDs use `EVI-YYYYMMDD-NNN`; obtain the next ID with `../shared/scripts/next_id.py EVI --root <workspace> --date YYYYMMDD`. Store packages under `证据包/YYYY-MM/` and update factual dossier fields by delta only.

Do not output bull/bear probability, research state, formal judgment, or trading instruction.

## Write boundary

May write evidence packages, factual dossier fields, and investigation reports. Must not write judgment outcomes, lesson states, or strategy parameters.
