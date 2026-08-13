---
name: a-share-investigate
description: Verify current A-share market, industry-chain, trading-theme, company, policy, and cross-market facts and create auditable immutable evidence packages. Use explicitly for factual research without forcing a directional judgment.
---

# A-share evidence investigation

Produce verified evidence; do not predict.

Read the workspace-root `研究规则.md` as the sole normative rule source and
`CONTEXT.md` for domain language. This skill defines investigation execution only.

Use the deep module in `../shared/context/` as the only workset boundary.
Instantiate the investigate contract, assemble required coverage, and hydrate
critical source locations. Raw web/PDF/API payloads belong in the external
source-payload store and must not be copied into the workset or evidence
package.

Follow the [阶段运行协议](../shared/contracts/README.md#阶段运行协议) for every routed or direct run.

## Start

From a router, reuse only its validated workspace and RUN ID. When called directly, validate the workspace and allocate a RUN ID before any persistent write. In both cases, create a fresh phase manifest with `workflow: investigate`, `stage: research`, canonical objects, a timezone-aware `information_cutoff`, the matching object-type task contract, and an independent workset-manifest target. Use its assembled workset to load `模板/证据包模板.md`, relevant dossier views, and cited evidence units. Freeze cutoff time, timezone, market dates, and data definitions.

The registered contracts use `prospective_current`. Dossier input is a set of eligible field atoms, not a file-level active snapshot. Do not implement expiry, invalidation, lifecycle, or supersession tests in the skill; inspect audited exclusions and hydrate only live stable references.

## Source discipline

- Search dynamically to avoid an information cocoon. Prefer original filings, exchange and regulator material, policy text, official statistics, issuer disclosures, and primary overseas sources; use high-quality financial reporting for context.
- Multiple reprints of one source count once. Preserve conflicts and lower completeness. Filter source-less predictions, SEO farms, AI aggregation, and unverifiable claims.
- Community, search, and media-frequency signals may measure narrative heat only. They cannot establish business facts or causality.
- Separate publication time from event time. Match timezone, units, adjustment method, and trading date before comparing values.

## Source payload seam

Externalize every raw web, PDF, API, or terminal payload before promoting a candidate fact:

1. Save the raw response to a phase-local input file and run `python3 ../shared/scripts/source_payload_store.py put --root <workspace> --run-id <RUN> --input-file <file> --acquired-at <ISO-8601-with-timezone> [--source-uri <uri>] [--content-type <type>]`. Missing or timezone-free acquisition time fails closed. Keep only the returned compact locator, including its auditable `acquired_at`, in the research context.
2. Use `source_payload_store.py locate` to recheck the locator and stored acquisition time. Use `source_payload_store.py excerpt` with explicit `--start-line`, `--end-line`, and `--max-chars` only to choose a bounded verification range; evidence coordinates use `line_start` and `line_end`.
3. Before a canonical evidence item exists, put that locator and range in the deliberately non-authoritative reference shape `ref=source-payload:<payload_id>`, `unit_id=<payload_id>`, `unit_type=source_payload_candidate`, `authority=source_payload_store`, and `status=unverified`. Set timezone-aware `information_cutoff` and `selection_cutoff`; the former must equal the locator's `acquired_at`. This candidate cannot claim objects, fields, evidence roles, relations, a canonical locator, or a canonical content hash, and it cannot enter a workset manifest.
4. Run `context_workspace.py hydrate --references <reference-json> --root <workspace>` before writing an atomic evidence item. The result is marked `verification_only`; it cannot cover a contract requirement or complete a workset. Hash, path, acquisition-time, range, decoding, or identity failure leaves the candidate unconfirmed, conflicting, or unavailable.
5. The evidence item cites the verified locator and source identity. After the canonical item exists, only its assembled full `atom:EVI-...` reference may hydrate the payload as evidence. The workset manifest contains the locator but never payload text, CLI excerpts, or hydrated verification text; it carries the locator only inside that canonical reference.
6. For a binary PDF, externalize the original but use a decodable page-text or structured-extraction payload as the evidence locator. Preserve page identity and source linkage. A binary-only locator that `excerpt` cannot decode fails closed and cannot support a confirmed item.

## Parallel research

For genuinely independent directions, use subagents for overseas markets, industry/policy, company filings, market structure, or counterevidence. Give each a non-overlapping question. Subagents may externalize raw payloads through the source-payload CLI under the assigned RUN, then return candidate atomic evidence and locators only; they must not write canonical workspace files or form the official conclusion. The main agent hydrates critical locators, deduplicates provenance, reconciles definitions, and performs the only canonical write.

## Workflow

1. Resolve object type: market, industry chain, trading theme, stock, or event. Load only the relevant object checklist from the workspace templates and domain model.
2. Compare with the previous dossier and evidence package. Report additions, strengthening, weakening, denial, maintenance, and expired fields.
3. Write one verifiable claim per atomic evidence item. Record status, independent source chain, event/publication time, market date, definition, related object/field, and expiry condition.
4. Separate confirmed facts, narrative signals, competing hypotheses, conflicts, denials, and missing evidence.
5. For stocks, verify E0–E4 realization level with dated primary evidence. For cross-market links, declare transmission tier, direction, lag, and validity window.
6. Mark completion as complete, partial, or unavailable. This status never becomes a directional conclusion.
7. Save a new immutable evidence package only when facts changed, a dossier was refreshed, or the package will enter a judgment chain. Otherwise return a read-only research summary.

## Output

Use `模板/证据包模板.md` for the canonical evidence package. IDs use `EVI-YYYYMMDD-NNN`; obtain the next ID with `../shared/scripts/next_id.py EVI --root <workspace> --date YYYYMMDD`. Store packages under `证据包/YYYY-MM/` and update factual dossier fields by delta only.

Do not output bull/bear probability, research state, formal judgment, or trading instruction.

## Write boundary

The research context may write evidence packages and factual dossier fields; the later presentation context may write investigation reports. Neither may write judgment outcomes, lesson states, or strategy parameters.

## Close research, then present

After the evidence package and factual dossier delta pass workspace validation, confirm that `hydrate` updated this investigate phase's independently persisted workset manifest. Put only validated atomic evidence IDs in the closure record; these become a later analysis manifest's `handoff.evidence_ids`. End the research context and release payload text, tool history, verification excerpts, and temporary reasoning.

Release removes payload text and open handles from the active context; it never deletes store files. A payload file and sidecar referenced by canonical evidence remain until the reference expires and its review is complete. No automatic cleanup is implemented.

Start a new presentation context with `模板/调研报告模板.md`, the closure record, and only canonical artifact IDs and stable references. Allocate its report ID with `../shared/scripts/next_id.py RPT --root <workspace> --date YYYYMMDD` before writing `报告/YYYY-MM/RPT-YYYYMMDD-NNN.md`. It may explain verified facts and recorded gaps without searching, adding evidence, changing status or completeness, or forming a direction. A presentation failure never creates a new evidence version or rewrites the validated package.
