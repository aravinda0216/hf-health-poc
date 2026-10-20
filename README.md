# Healthcare AI Platform Factory — Data engine + API engine (POC v0.1)

Scope: scenario **S1**, provider-facing, Medicare Advantage. Six use cases built: PCS claim status, PAS authorization status, PEV eligibility, PAP claim appeals, PPI payment inquiry, PON provider onboarding (4 scenarios each, 24 total).
Stubbed upstream: interpreter, blueprint engine (a sample blueprint JSON), orchestrator (`hf_factory/run.py`), infra engine (SQLite instead of AlloyDB/BigQuery/Vector Search).

    make install      # pip install -U -r requirements.txt (needs starlette>=0.40 with httpx>=0.28; else: TypeError ... unexpected keyword argument 'app')
    make demo         # 1,000 members, ~2 s:  blueprint -> data (s3-s7) -> API (s8) -> smoke (s10) -> delivery manifest
    make demo-full    # 50,000 members (blueprint scale), ~35 s, ~820k rows
    make test         # 62 contract tests (12 endpoints) + data tests
    make repro        # replay / re-seed / evolve checks
    make serve        # uvicorn on :8080 (fault injection on); client id hf-demo-client / secret demo-secret-change-me

## Layout
- `blueprints/s1_provider_demo.json` stub blueprint (hashes recomputed on load; `--members` patches the scale)
- `hf_factory/gen/` s3 `ref.py`, s4 `members.py`, s5 `claims_auth.py`, s6 `knowledge.py` (+ `assets.py` index layout)
- `hf_factory/rules.py` s7: 29 rules (R-01..11 subset, T-01..05/08, F-01..08, P-01/02, A-01, C-03/04); F-07 payee TIN = billing TIN except PPI-04, F-08 recoupment integrity, privacy scan, per-entity checksums, manifest
- `hf_factory/api/` FastAPI app, models, OpenAPI + tool-manifest builder
- `hf_factory/smoke.py` 24 scenarios (PCS/PAS/PEV/PAP/PPI/PON x 4); `hf_factory/repro.py` repeatability check
- `out/<run>/` blueprint, job specs, manifest.json, rule-report.json, openapi.yaml, tools.json, sample_calls.json, s10-smoke.json, delivery.json

## Determinism contract
Every row is a pure function of (root_seed, entity, row_index); ids are UUIDv5(dataset_id, entity, seed, index[, sub]); dates derive from `as_of_date`;
shard/worker index never enters a seed. `evolve` keeps `base_*` volumes so rows below the base are prefix-stable.
Derived tables (accumulators, care-gap closure, TOC/care cases, OOP-cap pass) are excluded from the prefix check; the OOP cap walks claims in claim-index order so it stays prefix-stable in practice.

## Deviations from the spec (decide or confirm)
1. **s6 runs after s5** (TOC rows and R-10 need claims); the doc lists s6 as parallel with s4.
2. **NPIs start with 9** (Luhn-valid, outside the real 1/2 range) so they cannot collide with real providers; the public-registry check is therefore not needed offline. Switch to registry-checked 1xxxxxxxxx NPIs if agents must see realistic IDs.
3. **All MA plans have $0 deductible**; the F-02 deductible term is present but always 0.
4. Pharmacy facility_type `PHARMACY` added to the provider enum; line-level `rendering_npi` added to `claim_line` (needed for CO-16 "missing rendering NPI on line 2").
5. Scenario ids added beyond the doc: `PCS-05` (CO-197 missing auth), `E2E-01` (appeal overturned and reprocessed), `TOC-01`.
6. Text is templated (no LLM); chunk text is short. Search is a local BM25-style index (Vertex AI Vector Search is the GCP mapping).
7. Provider claim-appeal deadline is 90 days (from the doc's PAP-01); validate with Compliance (C-05).
8. API "now" is the simulated clock (= as_of_date), so due dates and hours remaining match the dataset. Document uploads write to a separate state DB, never to the dataset, so checksums stay stable.
9. Provider use cases added: appeals (`createAppeal`, `getAppealStatus`), payments (`getClaimPayments`, `createPaymentCorrection`), onboarding (`getProviderOnboarding`, `updateLocationAvailability`). Late appeals need a good-cause statement and route to EXCEPTION_REVIEW; no outcome is ever promised. Payee-TIN and recoupment disputes only open a ticket (agents cannot change payees). Mutations (appeals, tickets, availability) go to the state DB, not the dataset.
10. Recoupments are separate negative `claim_payment` rows (`recoup_flag`, `offset_ref`); `claim_header` totals stay gross-paid, and the API reports gross, recouped and net separately. New denial reason `NETWORK` (CO-242) backs the PON-04 claims-load-lag scenario.
11. Not yet built: member-facing use cases (MBN, MCI, MPS, MPC, MAS, MPA, MBP, MRX), the other 6 personas' data, Member/Medicaid/Commercial LOBs, replay modes beyond replay/re-seed/evolve, X12/FHIR export, conversation-seed JSONL, Terraform/GCP mapping.

## Next steps for Claude Code
1. Add the remaining use cases (data entities are mostly present; add endpoints + scenarios + smoke tests per use case).
2. Swap SQLite writer for AlloyDB/BigQuery loaders behind `schema.Sink`; map knowledge search to Vertex AI Vector Search.
3. Replace templated text with LLM generation behind a content-addressed cache (`sha256(model, prompt_version, inputs, seed)`).
4. Add conversation seeds (transcript JSONL) from scenario rows and an LLM-driven agent eval on top of `smoke.py`.
