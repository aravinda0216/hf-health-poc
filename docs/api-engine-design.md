# API engine design

**Job:** from the `api_spec` plus the generated dataset, publish a working REST API, an OpenAPI 3.1 contract, an agent tool manifest and generated contract tests.

- **Generic backend (s8):** `use_cases` map through `api/catalog.py` to operationIds. Disabled routes are removed before mounting, so `openapi.yaml` and `tools.json` describe only what is live (S1: 12 operations, S2: 12, S4: 6).
- **Auth:** OAuth2 client credentials. Provider calls verify NPI + TIN; member calls are stateless and verify `dob` + `last_name`. Caregivers add `relationship_id` + `caregiver_name`; consent window and `authorization_scope` are enforced. Every verification failure returns the same 403 `VERIFICATION_FAILED` and discloses nothing.
- **Writes:** never touch the dataset; they go to a separate state store. Every write supports `dry_run`.
- **Safety:** fault injection (`x-inject-fault`), simulated clock (= `as_of_date`), rate limit, audit log, `is_synthetic` on every response.
- **Contract tests:** four per endpoint (auth required, invalid input rejected, response conforms to the OpenAPI schema, tool published).
- **Control plane (`hf_factory/control`):** stage job REST API for the orchestrator; checks seeds and spec hashes; runs persist under `HF_RUNS_DIR`.
- **Profiles (`HF_PROFILE`):** `local` (SQLite files), `memory` (dataset copied into RAM, state in RAM, nothing on disk), `gcp` (dataset pulled from `gs://`, state on the instance).
- **Knowledge search (`HF_SEARCH`):** pluggable backend in `hf_factory/search`: `lexical` BM25 (default), `vector` (in-memory embeddings; offline hashing embedder, not semantic) or `vertex` (Vertex AI embeddings + Vector Search, filtered by audience and use case). The API hydrates text and citations from the dataset by chunk id and abstains below the backend's threshold.
- **GCP status:** the Vertex AI and GCS code is unit-tested with fakes only and has not run against a live project. AlloyDB and a durable state store are not implemented. See the README "Run modes".
