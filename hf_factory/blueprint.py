"""Blueprint loading, scale patching and hashing (stub for the Blueprint Engine; deterministic, no LLM)."""
import copy, json
from .common import sha, canon, GEN_VERSION, CATALOG_VERSION, RULESET_VERSION

# Volumes follow simple ratios: ~6 claims per member per year, ~1 provider per 100 members, ~5% of claims have an auth.
def derive_volumes(members, months, base=None):
    claims = round(members * 6 * months / 12)
    v = dict(members=members, providers=max(60, round(members / 100 * 1.08)), claims=claims, auths=max(60, round(claims * 0.05)))
    # base_* = size at blueprint creation; rows below the base are prefix-stable under 'evolve' (members +20% etc.)
    b = base or v
    v.update(base_members=b["members"], base_providers=b["providers"], base_claims=b["claims"], base_auths=b["auths"])
    return v

def load(path, members=None, seed=None, evolve_from=None):
    bp = json.load(open(path))
    bp = patch(bp, members=members, seed=seed, evolve_from=evolve_from)
    return bp

def patch(bp, members=None, seed=None, evolve_from=None):
    bp = copy.deepcopy(bp)
    ds = bp["data_spec"]; months = ds["volumes"]["history_months"]
    n = members or ds["volumes"]["members"]
    base = None
    if evolve_from:  # evolve: keep the original size as the prefix-stable base
        base = derive_volumes(evolve_from, months)
    ds["volumes"] = {**derive_volumes(n, months, base), "history_months": months}
    if seed is not None: bp["repeatability"]["root_seed"] = int(seed)
    bp["blueprint_version"] = bp.get("blueprint_version", "1.0.0")
    finalize(bp)
    return bp

# The subset of the blueprint that determines the data. Only this is hashed into data_spec_hash, so unrelated blueprint edits don't change the dataset fingerprint.
def data_core(bp):
    r, ds = bp["repeatability"], bp["data_spec"]
    return {"dataset_id": ds["dataset_id"], "root_seed": r["root_seed"], "as_of_date": r["as_of_date"], "catalog_version": r["catalog_version"],
            "rule_set_version": r["rule_set_version"], "generator_version": r["generator_version"], "use_cases": bp["scope"]["use_cases"],
            "volumes": ds["volumes"], "edge": ds["edge_case_pct"], "lob": ds["distributions"]["lob_mix"]}

# Recompute both hashes after any patch so they always match the content (data_spec_hash first, then the whole-blueprint hash).
def finalize(bp):
    bp.pop("blueprint_hash", None); bp["data_spec"].pop("data_spec_hash", None)
    bp["data_spec"]["data_spec_hash"] = sha(data_core(bp))
    bp["blueprint_hash"] = sha(bp)
    return bp

def stage_seed(root_seed, stage_id):
    """Per-stage seed carried in every job payload. The control plane recomputes it and rejects a payload whose value differs."""
    from .common import h64
    return h64(root_seed, stage_id) & 0xFFFFFFFF

def job_spec(bp, stage, entities, extra=None, run_id=None):
    """Stub of the orchestrator slicing the blueprint into a per-stage job spec (shape from architecture doc).
    Carries everything the data engine needs to rebuild the run without the blueprint: dataset id, use cases, LOB mix, versions and both hashes."""
    r, ds = bp["repeatability"], bp["data_spec"]
    return {"job": stage["job"], "stage": stage["id"], "run_id": run_id or "run_" + bp["blueprint_hash"][7:19],
            "root_seed": r["root_seed"], "stage_seed": stage_seed(r["root_seed"], stage["id"]),
            "as_of_date": r["as_of_date"], "catalog_version": r["catalog_version"], "rule_set_version": r["rule_set_version"], "generator_version": r["generator_version"],
            "id_strategy": r["id_strategy"], "dataset_id": ds["dataset_id"], "use_cases": bp["scope"]["use_cases"], "lob_mix": ds["distributions"]["lob_mix"],
            "blueprint_hash": bp["blueprint_hash"], "data_spec_hash": ds["data_spec_hash"], "entities": entities,
            "volumes": ds["volumes"], "edge_case_pct": ds["edge_case_pct"], "rules": stage.get("rules", []), **(extra or {})}

def api_job_spec(bp, run_id=None):
    """Stub of the orchestrator's s8 job payload (shape from the architecture doc)."""
    a = bp["api_spec"]
    return {"job": "api_generate_and_deploy", "stage": "s8", "run_id": run_id or "run_" + bp["blueprint_hash"][7:19], "style": a["style"], "base_path": a["base_path"],
            "use_cases": bp["scope"]["use_cases"], "auth": {"type": a["auth"], "secret_ref": "env:HF_CLIENT_SECRET"}, "rate_limit": {"per_client_rpm": a["rate_limit_rpm"]},
            "fault_injection": a["fault_injection"], "agent_tools": a["agent_tools"]}

def from_job(j):
    """Rebuild the data-relevant part of a blueprint from an s3 job payload (what the control plane does, since it only receives job payloads).
    The data_spec_hash is recomputed from the payload and must equal the one the orchestrator sent."""
    bp = {"blueprint_id": "bp-" + j["run_id"], "scope": {"use_cases": j["use_cases"]},
          "repeatability": {"root_seed": j["root_seed"], "as_of_date": j["as_of_date"], "catalog_version": j["catalog_version"], "rule_set_version": j["rule_set_version"],
                            "generator_version": j["generator_version"], "id_strategy": j["id_strategy"]},
          "data_spec": {"dataset_id": j["dataset_id"], "volumes": j["volumes"], "edge_case_pct": j["edge_case_pct"], "distributions": {"lob_mix": j["lob_mix"]}}}
    finalize(bp)
    return bp

STAGES = [
 {"id":"s3","job":"generate_reference_data","rules":["R-04","F-04","P-02"]},
 {"id":"s4","job":"generate_members","rules":["R-02","R-05","R-06","T-01"]},
 {"id":"s5","job":"generate_claims_auth","rules":["R-03","R-07","F-01","F-02","F-05","T-02","T-03","T-04"]},
 {"id":"s6","job":"generate_care_knowledge","rules":["R-09","R-10","R-11","T-08"]},
 {"id":"s7","job":"validate_and_scan","rules":["ALL"]},
]
