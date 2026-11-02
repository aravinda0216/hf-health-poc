"""Data engine driver: s3 -> s4 -> s5 -> s6 -> s7 (sequential; s6 follows s5 because TOC rows derive from claims).

`DataRun` exposes one method per stage so the control plane can run them one job at a time;
`run()` is the one-shot convenience used by the CLI orchestrator stub and the tests.
"""
import json, os, time
from . import schema, rules, blueprint
from .common import Ctx
from .gen import ref, members, claims_auth, knowledge
from . import dynamic
from .dynamic import generate as dyn_gen

def _stage(fn):
    """Run a stage with the schema registry set to THIS run's custom entities. The registry is process-global, so stages of different runs (control plane) take turns."""
    def wrapper(self, *a, **k):
        with schema.LOCK:
            self._register(); return fn(self, *a, **k)
    wrapper.__name__ = fn.__name__; wrapper.__doc__ = fn.__doc__; return wrapper


STAGE_ORDER = ["s3", "s4", "s5", "s6", "s7"]


class DataRun:
    def __init__(self, bp, db_path, out_dir=None, log=print, resume_done=None):
        self.bp, self.db_path, self.out_dir, self.log = bp, db_path, out_dir, log
        ds, rp = bp["data_spec"], bp["repeatability"]
        self.vol = ds["volumes"]
        # Run context: everything derives from the blueprint's dataset_id, root_seed and as_of_date.
        self.ctx = Ctx(ds["dataset_id"], rp["root_seed"], rp["as_of_date"], ds["edge_case_pct"], self.vol)
        dist = ds.get("distributions", {}); self.ctx.lob_mix = dist.get("lob_mix", {"MEDICARE_ADV": 1}); self.ctx.dist = {k: dist[k] for k in blueprint.DIST_KEYS if k in dist}
        # resume_done: stages already completed in an earlier process (control-plane restart). The database is kept and the stage objects are rebuilt from the seed.
        # Blueprint-defined entities: validated, then registered in the schema so tables, indexes, rule R-01, checksums and the manifest pick them up.
        self.custom = dynamic.parse_spec(ds.get("custom_entities"), None, schema.core_cols())
        self.ctx.custom = self.custom
        with schema.LOCK:
            self._register()
            self.con = schema.connect(db_path, fresh=not resume_done)
            if not resume_done: schema.create_all(self.con)
        self.sink = schema.Sink(self.con, self.ctx)
        self.t0 = time.time(); self.done = list(resume_done or []); self.m = members.Members(self.ctx, self.vol) if resume_done else None; self.ca = None
        self.rep = self.man = self.scan = None; self.entity_rows = {}

    def _register(self):
        if self.custom: schema.register_custom(dyn_gen.schema_tables(self.custom))
        else: schema.unregister_custom()

    def _stamp(self, s): self.log(f"  {s:<34} {time.time()-self.t0:6.1f}s")

    def _count(self, tables):
        # Row counts per entity table, reported back to the orchestrator in each stage result.
        out = []
        for t in tables:
            try: n = self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception: n = 0
            out.append({"entity": t, "rows": n})
        return out

    def _need(self, prev):
        if prev not in self.done: raise RuntimeError(f"stage {prev} has not completed")

    @_stage
    def s3(self):
        # s3: reference data (providers, networks, locations).
        ref.generate(self.ctx, self.sink, self.vol); self.done.append("s3"); self._stamp("s3 reference data")
        return self._count(["plan", "provider", "practice_location", "appointment_slot", "network_participation", "ref_code"])

    @_stage
    def s4(self):
        # s4: members, plans, accumulators, PCP assignment and care gaps.
        self._need("s3"); self.m = members.Members(self.ctx, self.vol); self.m.generate(self.sink); self.done.append("s4"); self._stamp("s4 members")
        return self._count(["member_demographics", "plan_enrollment", "pcp_assignment", "family_relationship", "member_accumulator", "risk_score", "care_gap", "sdoh_indicator"])

    @_stage
    def s5(self):
        # s5: authorizations first, then claims (claims can reference auths). Indexes are created after the bulk insert for speed.
        self._need("s4"); self.ca = claims_auth.ClaimsAuth(self.ctx, self.vol, self.m); self.ca.emit_auth(self.sink); self.ca.emit_claims(self.sink)
        for s in schema.index_ddl(): self.con.execute(s)
        self.con.commit(); self.ca.derive(self.con, self.sink); self.done.append("s5"); self._stamp("s5 claims + authorizations")
        return self._count(["auth_request", "auth_line", "auth_determination", "clinical_review_queue", "documentation_requirement", "claim_header", "claim_line", "claim_payment", "claim_denial", "claim_pend", "claim_adjustment", "appeal_record", "reprocessing_history", "pharmacy_claim_detail"])

    @_stage
    def s6(self):
        # s6 runs after s5 because transition-of-care rows and rule R-10 need claims (the spec lists s6 as parallel with s4; see README deviations).
        self._need("s5"); knowledge.run(self.ctx, self.sink, self.con, self.vol, self.m)
        # Blueprint-defined entities are generated here, after claims and members exist, so their references resolve.
        if self.custom: dyn_gen.generate(self.custom, self.ctx, self.sink, self.con, self.log); self.sink.flush()
        self.done.append("s6"); self._stamp("s6 care + knowledge + custom entities")
        return self._count(["care_case", "transition_of_care", "knowledge_asset", "asset_chunk", "asset_link", "faq_entry", "agent_sop", "qa_golden"] + (list(schema.CUSTOM) if self.custom else []))

    @_stage
    def s7(self):
        # s7: run all rules, the privacy scan, and write checksums into the manifest.
        self._need("s6"); bp, ctx, con = self.bp, self.ctx, self.con
        self.rep = rules.build_rules(con, ctx, self.vol); self.scan = rules.privacy_scan(con); self.man = rules.manifest(con, ctx, bp, self.rep, self.scan)
        # _meta lets the API engine read as_of_date (its simulated clock) and the hashes without importing the generators.
        con.execute("CREATE TABLE IF NOT EXISTS _meta (k TEXT PRIMARY KEY, v TEXT)")
        con.executemany("INSERT OR REPLACE INTO _meta VALUES (?,?)", [("as_of_date", ctx.d(0)), ("dataset_id", ctx.dataset_id), ("blueprint_hash", bp["blueprint_hash"]),
                        ("data_spec_hash", bp["data_spec"]["data_spec_hash"]), ("root_seed", str(ctx.seed))])
        con.commit(); self.done.append("s7"); self._stamp("s7 validate + manifest")
        # The gate: every rule must pass AND the privacy scan must pass, otherwise the orchestrator stops before building the API.
        ok = all(r["status"] == "PASS" for r in self.rep.values()) and self.scan["status"] == "PASS"
        self.result = dict(status="COMPLETED" if ok else "FAILED_GATE", total_rows=self.man["total_rows"],
                           validation=dict(rules_checked=len(self.rep), passed=self.man["rules_passed"], failed=len(self.rep) - self.man["rules_passed"]), privacy_scan=self.scan["status"], db=self.db_path)
        if self.out_dir:
            os.makedirs(self.out_dir, exist_ok=True)
            json.dump(self.rep, open(os.path.join(self.out_dir, "rule-report.json"), "w"), indent=1)
            json.dump(self.man, open(os.path.join(self.out_dir, "manifest.json"), "w"), indent=1)
            json.dump(self.scan, open(os.path.join(self.out_dir, "privacy-scan.json"), "w"), indent=1)
        return self.result

    def close(self): self.con.close()


def run(bp, db_path, out_dir=None, log=print):
    r = DataRun(bp, db_path, out_dir, log)
    for st in STAGE_ORDER: getattr(r, st)()
    r.close()
    return r.result, r.rep, r.man
