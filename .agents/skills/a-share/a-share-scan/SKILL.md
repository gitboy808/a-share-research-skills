---
name: a-share-scan
description: Scan A-share industries, themes, and eligible main-board stocks for low-heat emergence, rotation, seesaw relationships, and catch-up candidates. Use explicitly for opportunity discovery; it produces time-bounded observation candidates, never formal predictions or recommendations.
---

# A-share opportunity scan

Optimize discovery recall without weakening formal judgment standards.

Context construction is delegated to `../shared/context/`. Instantiate the
scan task contract, call `context_workspace.py assemble`, and hydrate selected
stable references. Do not parse Markdown, query SQLite/FTS5, or treat semantic
adapter hits as evidence directly.

Follow the [阶段运行协议](../shared/contracts/README.md#阶段运行协议) for every routed or direct run.

## Start

From a router, reuse only its validated workspace and RUN ID. When called directly, validate the workspace and allocate a RUN ID before any persistent write. In both cases, create a fresh phase manifest with `workflow: scan`, `stage: scan`, canonical objects, a timezone-aware `information_cutoff`, the versioned scan task contract, and an independent workset-manifest target. Use its assembled workset to load `模板/观察候选模板.md`, relevant resident views, and cited atomic units. Freeze the scan cutoff and report data coverage.

The registered contract uses `prospective_current`. Do not add skill-local expiry, lifecycle, or version filtering; inspect assemble's audited exclusions and hydrate only live stable references.

## Scope

- Scan industries and trading themes across A shares.
- Proactive stock candidates are limited to Shanghai `60xxxx` and Shenzhen `000/001/002/003` main-board codes. Other boards remain sector evidence.
- Use current market, breadth, turnover, fund and catalyst data. Treat narrative sources only as attention measurements, never factual support.

## Workflow

1. Classify the market-state vector, funding environment, and rotation stage from hydrated atomic units. Mark unknown when independent evidence is insufficient.
2. Identify crowded directions and observable decay: shrinking breadth, leadership divergence, volume without price acceptance, narrative acceleration without funds, or lifecycle exhaustion.
3. Search for low-heat objects with improving relative strength, breadth, price acceptance, catalyst proximity, overseas validation, or defensive resilience.
4. Apply the heat-confirmation matrix. Low heat alone is not evidence of potential.
5. For a seesaw or catch-up thesis, require a rotation mechanism, applicable market/funding state, lag, and prior evidence cluster. Negative correlation alone is insufficient.
6. Create or version a candidate only with discovery evidence, confirmation trigger, invalidation, maximum acceptable price displacement, expected half-life, expiry, missing evidence, and investigation priority.
7. Rank the current view by discovery quality, catalyst distance, trigger proximity, and half-life. Keep no more than 20 active candidates in `观察池.md`; append all versions to `观察日志/YYYY-MM.md`.
8. A triggered candidate moves to investigation, never directly to a judgment.

## Output and scoring

Candidate IDs use `CYYYYMMDD-NNN`; obtain the next ID with `../shared/scripts/next_id.py C --root <workspace> --date YYYYMMDD`.

Do not assign bull/bear confidence or use recommendation language. Predeclare fields for effective discovery, late discovery, false positive, untriggered expiry, and missed-opportunity audit. Evaluate discovery rate, lead time, false positives, and misses separately.

## Write boundary

The research context may write observation candidates; the later presentation context may write scan reports. Neither may create evidence packages, formal judgments, lesson-state changes, or strategy parameters.

## Close research, then present

After observation-candidate writes pass workspace validation, confirm that `hydrate` updated this scan phase's independently persisted workset manifest and emit its phase closure record. End the research context and release source-payload text and handles, tool history, verification excerpts, and temporary reasoning from the active context; never delete a store entry still referenced by a canonical artifact.

Start a new presentation context with `模板/扫描报告模板.md`, the closure record, and only canonical artifact IDs and stable references. Allocate its report ID with `../shared/scripts/next_id.py RPT --root <workspace> --date YYYYMMDD` before writing `报告/YYYY-MM/RPT-YYYYMMDD-NNN.md`. It may write the scan report without searching, adding evidence, changing candidate fields, or creating judgments. A presentation failure never reopens or rewrites the validated candidate record.
