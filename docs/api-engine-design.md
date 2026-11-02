# API engine design

**Job:** from the `api_spec` plus the generated dataset, publish a working REST API, an OpenAPI 3.1 contract, an agent tool manifest and generated contract tests.

- **Generic backend (s8):** `use_cases` map through `api/catalog.py` to operationIds. Disabled routes are removed before mounting, so `openapi.yaml` and `tools.json` describe only what is live (S1: 12 operations, S2: 12, S4: 6).
- **Auth:** OAuth2 client credentials. Provider calls verify NPI + TIN; member calls are stateless and verify `dob` + `last_name`. Caregivers add `relationship_id` + `caregiver_name`; consent window and `authorization_scope` are enforced. Every verification failure returns the same 403 `VERIFICATION_FAILED` and discloses nothing.
- **Writes:** never touch the dataset; they go to a separate state store. Every write supports `dry_run`.
- **Safety:** fault injection (`x-inject-fault`), simulated clock (= `as_of_date`), rate limit, audit log, `is_synthetic` on every response.
- **Contract tests:** four per endpoint (auth required, invalid input rejected, response conforms to the OpenAPI schema, tool published).
- **Control plane (`hf_factory/control`):** stage job REST API for the orchestrator; checks seeds and spec hashes; runs persist under `HF_RUNS_DIR`.
- **Ports and profiles:** `ports.py` defines dataset, state and search interfaces. `HF_PROFILE=local` (SQLite) works. `HF_PROFILE=gcp` names AlloyDB / Firestore / Vertex AI Vector Search adapters that are **not implemented or tested** and fail with a clear error.
