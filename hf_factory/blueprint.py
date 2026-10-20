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

def job_spec(bp, stage, entities, extra=None):
    """Stub of the orchestrator slicing the blueprint into a per-stage job spec (shape from architecture doc)."""
    from .common import h64
    r, ds = bp["repeatability"], bp["data_spec"]
    return {"job": stage["job"], "stage": stage["id"], "root_seed": r["root_seed"], "stage_seed": h64(r["root_seed"], stage["id"]) & 0xFFFFFFFF,
            "as_of_date": r["as_of_date"], "catalog_version": r["catalog_version"], "id_strategy": r["id_strategy"], "entities": entities,
            "volumes": ds["volumes"], "edge_case_pct": ds["edge_case_pct"], "rules": stage.get("rules", []), **(extra or {})}

STAGES = [
 {"id":"s3","job":"generate_reference_data","rules":["R-04","F-04","P-02"]},
 {"id":"s4","job":"generate_members","rules":["R-02","R-05","R-06","T-01"]},
 {"id":"s5","job":"generate_claims_auth","rules":["R-03","R-07","F-01","F-02","F-05","T-02","T-03","T-04"]},
 {"id":"s6","job":"generate_care_knowledge","rules":["R-09","R-10","R-11","T-08"]},
 {"id":"s7","job":"validate_and_scan","rules":["ALL"]},
]
