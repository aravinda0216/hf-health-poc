"""Data engine driver: s3 -> s4 -> s5 -> s6 -> s7 (sequential; s6 follows s5 because TOC rows derive from claims)."""
import json, os, time
from . import schema, rules, blueprint
from .common import Ctx
from .gen import ref, members, claims_auth, knowledge

def run(bp, db_path, out_dir=None, log=print):
    ds, rp = bp["data_spec"], bp["repeatability"]
    vol = ds["volumes"]
    # Run context: everything derives from the blueprint's dataset_id, root_seed and as_of_date.
    ctx = Ctx(ds["dataset_id"], rp["root_seed"], rp["as_of_date"], ds["edge_case_pct"], vol)
    con = schema.connect(db_path, fresh=True); schema.create_all(con); sink = schema.Sink(con, ctx)
    t0 = time.time(); stamp = lambda s: log(f"  {s:<34} {time.time()-t0:6.1f}s")
    # s3: reference data (providers, networks, locations).
    ref.generate(ctx, sink, vol); stamp("s3 reference data")
    # s4: members, plans, accumulators, PCP assignment and care gaps.
    m = members.Members(ctx, vol); m.generate(sink); stamp("s4 members")
    # s5: authorizations first, then claims (claims can reference auths). Indexes are created after the bulk insert for speed.
    ca = claims_auth.ClaimsAuth(ctx, vol, m); ca.emit_auth(sink); ca.emit_claims(sink)
    for s in schema.index_ddl(): con.execute(s)
    con.commit(); ca.derive(con, sink); stamp("s5 claims + authorizations")
    # s6 runs after s5 because transition-of-care rows and rule R-10 need claims (the spec lists s6 as parallel with s4; see README deviations).
    knowledge.run(ctx, sink, con, vol, m); stamp("s6 care + knowledge")
    # s7: run all rules, the privacy scan, and write checksums into the manifest.
    rep = rules.build_rules(con, ctx, vol); scan = rules.privacy_scan(con); man = rules.manifest(con, ctx, bp, rep, scan); stamp("s7 validate + manifest")
    # _meta lets the API engine read as_of_date (its simulated clock) and the hashes without importing the generators.
    con.execute("CREATE TABLE _meta (k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO _meta VALUES (?,?)", [("as_of_date", ctx.d(0)), ("dataset_id", ctx.dataset_id), ("blueprint_hash", bp["blueprint_hash"]), ("data_spec_hash", ds["data_spec_hash"]), ("root_seed", str(ctx.seed))])
    con.commit()
    # The gate: every rule must pass AND the privacy scan must pass, otherwise the orchestrator stops before building the API.
    result = dict(status="COMPLETED" if all(r["status"] == "PASS" for r in rep.values()) and scan["status"] == "PASS" else "FAILED_GATE", total_rows=man["total_rows"],
                  validation=dict(rules_checked=len(rep), passed=man["rules_passed"], failed=len(rep) - man["rules_passed"]), privacy_scan=scan["status"], db=db_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        json.dump(rep, open(os.path.join(out_dir, "rule-report.json"), "w"), indent=1)
        json.dump(man, open(os.path.join(out_dir, "manifest.json"), "w"), indent=1)
        json.dump(scan, open(os.path.join(out_dir, "privacy-scan.json"), "w"), indent=1)
    con.close()
    return result, rep, man
