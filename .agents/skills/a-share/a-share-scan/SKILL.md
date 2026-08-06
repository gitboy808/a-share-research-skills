---
name: a-share-scan
description: Scan A-share industries, themes, and eligible main-board stocks for low-heat emergence, rotation, seesaw relationships, and catch-up candidates. Use explicitly for opportunity discovery; it produces time-bounded observation candidates, never formal predictions or recommendations.
---

# A-share opportunity scan

Optimize discovery recall without weakening formal judgment standards.

## Start

If no reusable router manifest exists, perform the preflight in `../a-share-research/SKILL.md`. Read `模板/扫描报告模板.md`, `模板/观察候选模板.md`, `观察池.md`, related dossiers, `策略库/索引.md`, and current market lessons. Freeze the scan cutoff and report data coverage.

## Scope

- Scan industries and trading themes across A shares.
- Proactive stock candidates are limited to Shanghai `60xxxx` and Shenzhen `000/001/002/003` main-board codes. Other boards remain sector evidence.
- Use current market, breadth, turnover, fund and catalyst data. Treat narrative sources only as attention measurements, never factual support.

## Workflow

1. Classify the market-state vector, funding environment, and rotation stage. Mark unknown when independent evidence is insufficient.
2. Identify crowded directions and observable decay: shrinking breadth, leadership divergence, volume without price acceptance, narrative acceleration without funds, or lifecycle exhaustion.
3. Search for low-heat objects with improving relative strength, breadth, price acceptance, catalyst proximity, overseas validation, or defensive resilience.
4. Apply the heat-confirmation matrix. Low heat alone is not evidence of potential.
5. For a seesaw or catch-up thesis, require a rotation mechanism, applicable market/funding state, lag, and prior evidence cluster. Negative correlation alone is insufficient.
6. Create or version a candidate only with discovery evidence, confirmation trigger, invalidation, maximum acceptable price displacement, expected half-life, expiry, missing evidence, and investigation priority.
7. Rank the current view by discovery quality, catalyst distance, trigger proximity, and half-life. Keep no more than 20 active candidates in `观察池.md`; append all versions to `观察日志/YYYY-MM.md`.
8. A triggered candidate moves to investigation, never directly to a judgment.

## Output and scoring

Use `模板/扫描报告模板.md`. Candidate IDs use `CYYYYMMDD-NNN`; obtain the next ID with `../shared/scripts/next_id.py C --root <workspace> --date YYYYMMDD`.

Do not assign bull/bear confidence or use recommendation language. Predeclare fields for effective discovery, late discovery, false positive, untriggered expiry, and missed-opportunity audit. Evaluate discovery rate, lead time, false positives, and misses separately.

## Write boundary

May write observation candidates and scan reports. Must not create evidence packages, formal judgments, lesson-state changes, or strategy parameters.
