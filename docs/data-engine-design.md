# Data engine design

**Job:** turn the `data_spec` of a blueprint into a synthetic, repeatable, privacy-safe dataset (35 entities + `ref_code`), and prove it is consistent before anything is built on it.

## Stages (control plane job names)
| Stage | Output | Notes |
|---|---|---|
| s3 | plans, providers, locations, network participation, appointment slots, reference codes | LOB-aware plans; geography (urban/suburban/rural) per `geo_mix` |
| s4 | members, enrollment, family/caregiver relationships, PCP assignment, accumulators, risk, care gaps, SDOH | age and persona follow the plan's LOB; language and caregiver rate from `distributions` |
| s5 | authorizations, claims, lines, payments, denials, pends, appeals, reprocessing | `denial_rate`, `pend_rate`, `claims_per_member_year` from the blueprint |
| s6 | care cases, transition of care, knowledge assets/chunks, FAQs, SOPs, golden Q&A | includes member guides and member FAQ libraries |
| s7 | 31 rules, privacy scan, per-entity checksums, `manifest.json`, `_meta` | the gate: any failed rule or privacy hit stops the pipeline |

s6 follows s5 (transition-of-care rows need claims); the architecture lists it as parallel to s4.

## Determinism contract
Every row is a pure function of `(root_seed, stage, entity, index)`. Ids are UUIDv5. Dates derive from `as_of_date`. Checksums are order-independent (sum of 64-bit row hashes), so sharding can be added later without changing results. `evolve` keeps `base_*` volumes so earlier rows are prefix-stable. Per-stage seeds are `blake2b(root_seed, stage) & 0xFFFFFFFF`; the control plane recomputes them and the `data_spec_hash` and rejects mismatches.

## Distributions
`lob_mix` (largest-remainder split of 4 plans), `language_mix`, `geo_mix`, `caregiver_rate`, `denial_rate`, `pend_rate`, `claims_per_member_year`, `edge_case_pct`. The Spanish share of providers rises with the member `ES` share. Checked in `tests/test_scenarios.py`.

## Known limits
Text is templated (no LLM). No deductibles. Assigned PCPs are often beyond 50 miles. Specialist-referral rules, MPA, MRX and MBP are not built. No sharding or failed-shard retry: each stage runs in one process.

## Blueprint-defined entities (`hf_factory/dynamic`)
`data_spec.custom_entities` adds tables without code: `spec.py` validates the definitions, `schema.register_custom` adds them to the schema registries (tables, foreign keys, order), `generate.py` produces rows (generated in s6, after members and claims exist so references resolve), and `rules.py` adds rules X-01..X-06. Rows are pure functions of `(root_seed, "sx", table, row index)`; `unique` constraints are met by deterministic retries. Custom tables are excluded from the evolve prefix check because their counts can depend on parent tables. See the README for the full format and the limits.
