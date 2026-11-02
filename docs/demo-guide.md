# Demo guide

    make install
    make demo                                   # S1 provider, 1,000 members, ~2 s
    python3 -m hf_factory.run --blueprint blueprints/s2_medicaid_member.json --members 1000 --out out/s2
    python3 -m hf_factory.run --blueprint blueprints/s4_commercial_sandbox.json --members 1000 --out out/s4
    python3 -m hf_factory.run --blueprint blueprints/s3_commercial_scale.json --members 5000 --out out/s3   # omit --members for 150,000
    HF_DB=out/s2/factory.db make serve          # API on :8080 (hf-demo-client / demo-secret-change-me)
    make serve-control & make orchestrate       # drive s3-s8 through the control plane

Postman: `postman/hf-provider-api.postman_collection.json` (S1) and `postman/hf-member-api.postman_collection.json` (S2/S4; regenerate with `python3 postman/generate_member.py <db>`).

| Scenario | Blueprint | Shape |
|---|---|---|
| S1 | `s1_provider_demo.json` | Medicare Advantage, provider use cases, 50k members |
| S2 | `s2_medicaid_member.json` | Medicaid, member use cases, 35% Spanish, 40% rural, 25% caregivers, 15% edge, 100k members |
| S3 | `s3_commercial_scale.json` | Commercial, 150k members, ~1M claims, 30% denials, 30% edge |
| S4 | `s4_commercial_sandbox.json` | Commercial sandbox, 20k members, MBN/MCI/PCS |

In-memory: `make serve-memory`. GCP: see the README "Run modes" (`gcp_publish`, then `make serve-gcp`).

Docker: `docker build -t hf-factory .` (add `--build-arg INSTALL_GCP=1` for GCP), run with `HF_ROLE=api|control|job|publish`, or `docker compose up`. The image has not been built (no Docker daemon here); the entrypoint was exercised directly.
