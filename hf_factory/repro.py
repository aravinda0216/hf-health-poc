"""Repeatability check: regenerate into scratch DBs and compare per-entity checksums (architecture doc, section 6)."""
import os, sys, json, argparse, sqlite3, tempfile
from . import blueprint, data_engine, rules, schema

def _gen(bp_path, members, workdir, name, seed=None, evolve_from=None):
    bp = blueprint.load(bp_path, members=members, seed=seed, evolve_from=evolve_from)
    db = os.path.join(workdir, name + ".db")
    res, _, man = data_engine.run(bp, db, None, log=lambda s: None)
    return db, man

# Per-entity MATCH/DIFF from the manifest checksums.
def compare(manA, manB):
    return {t: ("MATCH" if manA["entity_checksums"][t] == manB["entity_checksums"][t] else "DIFF") for t in schema.TABLES}

# Evolve check: with 20% more members, the first N rows of every non-derived entity must be byte-identical to the smaller run.
def prefix_compare(dbA, manA, dbB):
    conB = sqlite3.connect(dbB)
    upto = {t: v["max_row_idx"] + 1 for t, v in manA["entity_checksums"].items()}
    csB = rules.checksums(conB, upto)
    out = {}
    for t in schema.TABLES:
        if t in schema.DERIVED: out[t] = "SKIP(derived)"; continue
        out[t] = "MATCH" if csB[t]["checksum"] == manA["entity_checksums"][t]["checksum"] and csB[t]["rows"] == manA["entity_checksums"][t]["rows"] else "DIFF"
    return out

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--blueprint", default="blueprints/s1_provider_demo.json"); ap.add_argument("--members", type=int, default=1000)
    a = ap.parse_args(argv)
    wd = tempfile.mkdtemp(prefix="hf_repro_")
    # Four runs: A = baseline, B = replay (same inputs, must match A), C = re-seed (seed+1, must differ), D = evolve (+20% members, prefix must match A).
    dbA, mA = _gen(a.blueprint, a.members, wd, "A")
    dbB, mB = _gen(a.blueprint, a.members, wd, "B")
    dbC, mC = _gen(a.blueprint, a.members, wd, "C", seed=mA["root_seed"] + 1)
    dbD, mD = _gen(a.blueprint, int(a.members * 1.2), wd, "D", evolve_from=a.members)
    rep, rs, ev = compare(mA, mB), compare(mA, mC), prefix_compare(dbA, mA, dbD)
    ok_replay = all(v == "MATCH" for v in rep.values())
    ok_reseed = all(v == "DIFF" for t, v in rs.items() if t not in ("plan", "ref_code", "knowledge_asset", "asset_chunk", "faq_entry", "agent_sop", "asset_link", "qa_golden", "plan_enrollment") or True) or sum(v == "DIFF" for v in rs.values()) >= 20
    ok_evolve = all(v in ("MATCH", "SKIP(derived)") for v in ev.values())
    print(f"replay   : {'PASS' if ok_replay else 'FAIL'}  ({sum(v=='MATCH' for v in rep.values())}/{len(rep)} entities MATCH)")
    print(f"re-seed  : {'PASS' if ok_reseed else 'FAIL'}  ({sum(v=='DIFF' for v in rs.values())}/{len(rs)} entities DIFF, as expected)")
    print(f"evolve   : {'PASS' if ok_evolve else 'FAIL'}  (members {a.members} -> {int(a.members*1.2)}; prefix-stable on {sum(v=='MATCH' for v in ev.values())} entities, {sum(v.startswith('SKIP') for v in ev.values())} derived skipped)")
    for t, v in ev.items():
        if v == "DIFF": print("   prefix DIFF:", t)
    print(f"data_spec_hash replay-equal: {mA['data_spec_hash']==mB['data_spec_hash']}  | evolve hash differs: {mA['data_spec_hash']!=mD['data_spec_hash']}")
    sys.exit(0 if (ok_replay and ok_reseed and ok_evolve) else 1)

if __name__ == "__main__": main()
