# Healthcare AI Platform Factory — Data engine + API engine (POC v0.1)

Scenarios **S1-S5** (blueprints/). **New data and API capabilities are defined in the blueprint, not in code** (see "Blueprint-defined entities and APIs"). Provider use cases: PCS, PAS, PEV, PAP, PPI, PON. Member use cases: MBN, MCI, MPS, MPC, MAS (4 smoke scenarios each, 44 total). Lines of business: Medicare Advantage, Medicaid MCO, Commercial.
Also: blueprint-driven entities/endpoints (`hf_factory/dynamic`), control plane (`hf_factory/control`), run profiles (`local`, `memory`, `gcp`), pluggable knowledge search (`lexical`, in-memory `vector`, Vertex AI), Dockerfile, design docs in `docs/`.
Stubbed upstream: interpreter, blueprint engine (sample blueprint JSONs), orchestrator (`hf_factory/run.py`), infra engine (SQLite, or GCS + Vertex AI on the `gcp` profile).

    make install      # pip install -U -r requirements.txt (needs starlette>=0.40 with httpx>=0.28; else: TypeError ... unexpected keyword argument 'app')
    make demo         # 1,000 members, ~2 s:  blueprint -> data (s3-s7) -> API (s8) -> smoke (s10) -> delivery manifest
    make demo-full    # 50,000 members (blueprint scale), ~35 s, ~820k rows
    make test         # 148 tests
    make repro        # replay / re-seed / evolve checks
    make serve        # uvicorn on :8080 (fault injection on); client id hf-demo-client / secret demo-secret-change-me
    make serve-memory # same API fully in memory (dataset copied into RAM, state gone on exit, in-memory vector search)
    make serve-gcp    # dataset from Cloud Storage, knowledge search on Vertex AI (see "Run modes")
    make docker-build # docker build -t hf-factory .

## Blueprint-defined entities and APIs
The engines are driven by the blueprint, not only by switches over pre-written code. Add an entity under `data_spec.custom_entities` and endpoints under `api_spec.custom_endpoints`; the factory turns them into a table, deterministic records, validation rules, live routes, OpenAPI, agent tools, contract tests and smoke scenarios. **No Python is written per entity.** `blueprints/s5_custom_appointments.json` does exactly this for 15,000 members with claims (PCS) and eligibility (PEV) APIs plus an Appointment entity and `POST /appointments`:

    "custom_entities": [{ "name": "Appointment", "count": {"ratio": {"of": "member_demographics", "value": 0.8}},
      "fields": [ {"name": "appointment_id",   "type": "id", "primary_key": true},
                  {"name": "member_id",        "type": "ref", "ref": "member_demographics.member_id"},
                  {"name": "provider_id",      "type": "ref", "ref": "provider.npi"},
                  {"name": "appointment_date", "type": "date", "offset_days": [-30, 60]},
                  {"name": "start_time",       "type": "enum", "values": ["08:00", "08:30", "09:00"]},
                  {"name": "status",           "type": "enum", "values": {"BOOKED": 70, "COMPLETED": 20, "CANCELLED": 10}, "default": "BOOKED", "server_set": true} ],
      "unique": [["provider_id", "appointment_date", "start_time"]], "unique_exempt": {"status": ["CANCELLED"]} }]
    "custom_endpoints": [
      {"method": "POST",  "path": "/appointments",                  "entity": "Appointment", "operation": "create", "verify_member": "member_id"},
      {"method": "GET",   "path": "/appointments/{appointment_id}", "entity": "Appointment", "operation": "get",    "verify_member": "member_id"},
      {"method": "GET",   "path": "/appointments",                  "entity": "Appointment", "operation": "list",   "filters": ["member_id", "status"], "verify_member": "member_id"},
      {"method": "PATCH", "path": "/appointments/{appointment_id}", "entity": "Appointment", "operation": "update", "updatable": ["status"], "transitions": {"BOOKED": ["CANCELLED", "COMPLETED"]}, "verify_member": "member_id"} ]

    python3 -m hf_factory.run --blueprint blueprints/s5_custom_appointments.json --out out/s5       # 15,000 members, ~9 s
    HF_DB=out/s5/factory.db HF_CUSTOM_SPEC=out/s5/custom-spec.json make serve                        # serve it

**What the blueprint can say**

| Part | Options |
|---|---|
| Field `type` | `id` (UUID), `string` (`pattern` like `APT-{seq:6}`, or `generator`: first_name, last_name, full_name, city, state, phone, email, sentence), `text`, `int`, `float` (`min`, `max`, `mean`, `sd`), `bool` (`p`), `date` / `datetime` (`offset_days` from the as-of date, or `after` another field), `enum` (list, or `{value: weight}`), `ref` (`table.column` in the built-in schema or another custom entity), `json` |
| Field flags | `primary_key`, `required`, `null_rate`, `default`, `server_set` (not accepted from callers), `description` |
| `count` | `fixed`, `ratio` of another table, or `per_parent` (min..max children per row of a parent table; the ref field to that parent is assigned) |
| Entity | `unique` (composite), `unique_exempt` (values that free the slot, e.g. CANCELLED), `scenario_id` |
| Endpoint `operation` | `create` (POST), `get`, `list` (GET, with `filters`, `limit`, `offset`), `update` (PATCH, `updatable` fields, enum `transitions`) |
| Endpoint options | `verify_member` (caller must give that member's date of birth and last name; any mismatch is the standard 403 that discloses nothing), `summary`, `operation_id`, agent-tool `when` / `escalate` / `never` |

**What is generated**
- **Data engine:** table and indexes (registered in the same schema as the built-in tables, so R-01 orphan checks, checksums and the manifest cover them); records generated deterministically in dependency order; six generated rules, X-01 primary keys, X-02 required fields, X-03 enum values, X-04 numeric ranges, X-05 unique constraints, X-06 `after` dates. Definitions are validated up front with errors that name the entity and field. The definition is part of `data_spec_hash`.
- **API engine:** routes with typed, range-checked request models; references must exist (404 `REFERENCE_NOT_FOUND`), unique clash is 409 `CONFLICT`, `dry_run` on writes, idempotent ids (repeating a request returns the same record), illegal status change is 409 `INVALID_TRANSITION`; OpenAPI and agent-tool entries with guardrails; four generated contract tests per endpoint; generated smoke scenarios (create and read back, invalid or unverified request rejected, idempotent repeat, unique clash, allowed transitions, appears in list). Writes go to the state store, never the dataset; a dataset row that is updated is copied to state and read from there.
- **Control plane:** the s3..s7 and s8 payloads carry `custom_entities` / `custom_endpoints`; bad definitions are rejected with 422 `INVALID_CUSTOM_SPEC`.

**Limits (honest)**
- The built-in entities (members, claims, authorizations, providers, appeals, ...) and their endpoints are still hand-written. The custom layer is additive; it can reference them but does not replace them.
- Generated endpoints are create / get / list / update with declarative rules: types, ranges, enums, references, uniqueness, status transitions and member verification. Business logic beyond that (availability windows, a 24-hour cancellation rule, benefit calculations) is not generated from a definition; it still needs code or a new declarative rule type.
- There is no LLM in the loop: the definition is turned into code paths deterministically, so a request in free text must first become a blueprint definition (the interpreter and blueprint engine are separate work).
- Custom tables hold generated data in the dataset; created or changed records live in the state store (ephemeral on Cloud Run).
- The schema registry is process-global; data stages of different runs in one control-plane process take turns.

## Run modes
`HF_PROFILE` picks where data lives; `HF_SEARCH` picks how knowledge search works. The API code is identical in every mode.

| `HF_PROFILE` | Dataset | Caller-created state (appeals, bookings, tickets) | Default search |
|---|---|---|---|
| `local` (default) | SQLite file, read-only | SQLite file (`HF_STATE_DB`, default `<db>.state`) | `lexical` |
| `memory` | SQLite file copied into RAM at start, read-only | in RAM, lost on exit, nothing written to disk | `lexical` |
| `gcp` | SQLite file pulled once from `HF_DATASET_URI=gs://bucket/path/factory.db` | SQLite on the instance (ephemeral on Cloud Run) | `vertex` |

| `HF_SEARCH` | How it works | Notes |
|---|---|---|
| `lexical` | BM25-style with stemming, idf-weighted coverage and an abstain threshold | best recall on the templated corpus (>= 85% of EASY golden questions) |
| `vector` | embed chunks and queries, nearest neighbours in memory (`HF_VECTOR_SCORE_MIN`, default 0.17) | offline stand-in: a tf-idf feature-hashing embedder, **not semantic**. Never answers the unanswerable golden questions; answers ~70% of EASY ones |
| `vertex` | Vertex AI text embeddings (`text-embedding-005`) + Vertex AI Vector Search (`HF_VERTEX_SCORE_MIN`, default 0.55) | needs `requirements-gcp.txt`; **not verified against a live project** |

All backends return only chunk ids and scores; the API reads text, titles and citations from the dataset, filters by audience and use case, and abstains below the threshold.

### In-memory mode
    make demo && make serve-memory
No files are written. Combine with `HF_SEARCH=lexical|vector`. Restarting loses appeals, bookings and tickets by design.

### GCP mode (Cloud Storage + Vertex AI)
Prerequisites: a GCP project with the Vertex AI and Cloud Storage APIs enabled, a bucket, Application Default Credentials (`gcloud auth application-default login`, or a Cloud Run service account with Storage Object Viewer and Vertex AI User), `pip install -r requirements-gcp.txt`.

    # 1. one-off: create the Vector Search index (768-d, DOT_PRODUCT_DISTANCE, STREAM_UPDATE), a public endpoint and deploy it (takes a while and bills while deployed)
    python -m hf_factory.gcp_publish --create-index --project $P --region us-central1      # prints HF_VERTEX_* values
    # 2. per dataset: upload the SQLite dataset and embed + upsert its ACTIVE, in-date knowledge chunks
    python -m hf_factory.gcp_publish --db out/s2/factory.db --project $P --dataset-uri gs://$B/s2/factory.db --index $HF_VERTEX_INDEX
    # 3. serve
    HF_PROFILE=gcp HF_DATASET_URI=gs://$B/s2/factory.db HF_GCP_PROJECT=$P HF_GCP_REGION=us-central1 \
    HF_VERTEX_INDEX_ENDPOINT=... HF_VERTEX_DEPLOYED_INDEX_ID=... make serve-gcp

| Variable | Used by | Meaning |
|---|---|---|
| `HF_PROFILE`, `HF_SEARCH` | API | profile and search backend (above) |
| `HF_DB` / `HF_DATASET_URI` | API | dataset file, or `gs://` object (gcp) |
| `HF_STATE_DB` | API | state file (local, gcp) |
| `HF_GCP_PROJECT`, `HF_GCP_REGION` | Vertex, GCS | project and region (default region `us-central1`) |
| `HF_VERTEX_EMBED_MODEL`, `HF_VERTEX_DIM` | Vertex | embedding model (`text-embedding-005`) and dimension (768) |
| `HF_VERTEX_INDEX_ENDPOINT`, `HF_VERTEX_DEPLOYED_INDEX_ID` | API | the deployed index that answers queries |
| `HF_VERTEX_INDEX` | publish | the index that receives streaming upserts |
| `HF_VERTEX_SCORE_MIN`, `HF_VECTOR_SCORE_MIN`, `HF_KB_SCORE_MIN`, `HF_KB_COVERAGE_MIN` | API | abstain thresholds; tune `HF_VERTEX_SCORE_MIN` against the golden questions once the index exists |

What is and is not done on GCP: the Vertex AI embedding and Vector Search code (query, filtered by audience and use case; publish; index creation) and the GCS dataset download follow the installed `google-cloud-aiplatform` / `google-cloud-storage` signatures and are unit-tested with fakes (`tests/test_gcp_memory.py`), but nothing has run against a real project. A durable state store (Firestore / Cloud SQL) and AlloyDB for the dataset are not implemented; on Cloud Run, appeals, bookings and tickets are lost when the instance is replaced.

### Docker
    docker build -t hf-factory .                                  # local and memory profiles
    docker build --build-arg INSTALL_GCP=1 -t hf-factory-gcp .    # adds the Google Cloud libraries
    docker run -p 8080:8080 -v $PWD/out:/data:ro -e HF_DB=/data/dev/factory.db -e HF_PROFILE=memory hf-factory
    docker compose up                                              # api (memory) on :8080 and control plane on :9000
`HF_ROLE` selects the process: `api` (default), `control` (stage-job API), `job` (one-shot blueprint run, output in `/data/out`), `publish` (arguments go to `gcp_publish`). The Dockerfile and compose file have **not been built or run** (no Docker daemon in the build environment); the entrypoint script was exercised directly.

## Layout
- `blueprints/s1_provider_demo.json` stub blueprint (hashes recomputed on load; `--members` patches the scale)
- `hf_factory/gen/` s3 `ref.py`, s4 `members.py`, s5 `claims_auth.py`, s6 `knowledge.py` (+ `assets.py` index layout)
- `hf_factory/rules.py` s7: 31 rules (R-01..11 subset, T-01..05/08, F-01..08, P-01/02, A-01, C-03/04); F-07 payee TIN = billing TIN except PPI-04, F-08 recoupment integrity, privacy scan, per-entity checksums, manifest
- `hf_factory/api/` FastAPI app, models, OpenAPI + tool-manifest builder
- `hf_factory/search/` knowledge-search backends (`lexical`, `vector`, `vertex`); `hf_factory/profiles.py` + `adapters/` run profiles; `hf_factory/gcp_publish.py` GCS + Vertex publisher
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
6. Text is templated (no LLM); chunk text is short. Search defaults to a local BM25-style index; in-memory vectors and Vertex AI Vector Search are selectable (see Run modes).
7. Provider claim-appeal deadline is 90 days (from the doc's PAP-01); validate with Compliance (C-05).
8. API "now" is the simulated clock (= as_of_date), so due dates and hours remaining match the dataset. Document uploads write to a separate state DB, never to the dataset, so checksums stay stable.
9. Provider use cases added: appeals (`createAppeal`, `getAppealStatus`), payments (`getClaimPayments`, `createPaymentCorrection`), onboarding (`getProviderOnboarding`, `updateLocationAvailability`). Late appeals need a good-cause statement and route to EXCEPTION_REVIEW; no outcome is ever promised. Payee-TIN and recoupment disputes only open a ticket (agents cannot change payees). Mutations (appeals, tickets, availability) go to the state DB, not the dataset.
10. Recoupments are separate negative `claim_payment` rows (`recoup_flag`, `offset_ref`); `claim_header` totals stay gross-paid, and the API reports gross, recouped and net separately. New denial reason `NETWORK` (CO-242) backs the PON-04 claims-load-lag scenario.
11. Not yet built: MPA, MBP, MRX, deductibles, specialist-referral rules, sharding and failed-shard retry, X12/FHIR export, conversation-seed JSONL, agentic capability layer, a live-verified GCP deployment, durable state (Firestore / Cloud SQL), AlloyDB, and Terraform. The Dockerfile has not been built (no daemon here).

## Next steps for Claude Code
1. Add the remaining use cases (data entities are mostly present; add endpoints + scenarios + smoke tests per use case).
2. Run the GCP mode against a real project and tune `HF_VERTEX_SCORE_MIN`; swap the SQLite writer for AlloyDB/BigQuery loaders behind `schema.Sink`; add a durable state store.
3. Replace templated text with LLM generation behind a content-addressed cache (`sha256(model, prompt_version, inputs, seed)`).
4. Add conversation seeds (transcript JSONL) from scenario rows and an LLM-driven agent eval on top of `smoke.py`.

## Postman
`postman/hf-provider-api.postman_collection.json` (42 requests, 155 assertions, provider API) and `postman/hf-member-api.postman_collection.json` (21 requests, 34 assertions, member API; `python3 postman/generate_member.py out/s2/factory.db`), with `postman/hf-local.postman_environment.json`.
Run `make demo`, then `make serve`, import both files into Postman and run the collection folders in order; a token is fetched automatically.
`postman/generate.py` rebuilds the files from the dataset (`python3 postman/generate.py out/dev/factory.db`). Some requests change state (file an appeal, submit a
document, open a ticket, set availability), so restart `make serve` before re-running. CLI: `newman run postman/hf-provider-api.postman_collection.json -e postman/hf-local.postman_environment.json`.
