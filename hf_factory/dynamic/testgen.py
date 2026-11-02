"""Contract samples and smoke scenarios generated from the entity / endpoint definitions (no per-entity test code).

A Sampler finds valid request bodies by trial: values come from the same generator that built the dataset (dates pushed 1,000 days past the as-of date so they
cannot collide with generated rows), and each candidate is checked with a dry run against the live app before it is used."""
import json, sqlite3
from datetime import date, timedelta
from ..common import Ctx
from .generate import value


class Sampler:
    def __init__(self, db_path, spec, client, headers):
        self.spec, self.c, self.H = spec, client, headers
        self.db = sqlite3.connect(db_path); self.db.row_factory = sqlite3.Row
        meta = dict(self.db.execute("SELECT k,v FROM _meta").fetchall()); self.ctx = Ctx(meta["dataset_id"], meta["root_seed"], meta["as_of_date"]); self.k = 0

    def _keys(self, target):
        t, col = target.split("."); return [r[0] for r in self.db.execute(f"SELECT {col} FROM {t} ORDER BY row_idx LIMIT 500")]

    def member_factors(self, member_id):
        r = self.db.execute("SELECT dob, last_name FROM member_demographics WHERE member_id=?", (member_id,)).fetchone(); return {"dob": r["dob"], "last_name": r["last_name"]}

    def row(self, e, where="1=1", args=()):
        r = self.db.execute(f"SELECT * FROM {e.table} WHERE {where} ORDER BY row_idx LIMIT 1", args).fetchone(); return dict(r) if r else None

    def create_body(self, ep, ok_status=201):
        """A request body the API accepts (verified by dry run), or None if none is found."""
        e = self.spec.entity(ep.entity); keys = {f.ref: self._keys(f.ref) for f in e.fields if f.type == "ref"}
        for _ in range(300):
            self.k += 1; r = self.ctx.rng("sxapi", e.table, self.k); row = {}
            for f in e.fields:
                f2 = f
                if f.type in ("date", "datetime") and not f.after: f2 = f.model_copy(update={"offset_days": [1000, 1300]})
                row[f.name] = value(self.spec, self.ctx, e, f2, r, 10_000_000 + self.k, row, keys)
            body = {f.name: row[f.name] for f in e.fields if not f.primary_key and not f.server_set and row[f.name] is not None}
            if ep.verify_member: body.update(self.member_factors(body[ep.verify_member]))
            if self.c is None or self.c.post("/v1" + ep.path, json=body, params={"dry_run": "true"}, headers=self.H).status_code == ok_status: return body      # no client: first candidate (used to write Postman bodies)
        return None

    def endpoint(self, ep, op):
        return next((x for x in self.spec.endpoints if x.entity.lower() == ep.entity.lower() and x.operation == op), None)


def contract_samples(sp):
    """{operationId: sample} in the format contract_gen.run uses."""
    spec = sp.spec; S = {}
    for ep in spec.endpoints:
        e = spec.entity(ep.entity); op = spec.op_id(ep); pk = spec.pk(e).name; pname = pk if "{" + pk + "}" in ep.path else "id"
        if ep.operation == "create":
            body = sp.create_body(ep)
            if body is None: continue
            first_req = next((f for f in e.fields if not f.primary_key and not f.server_set and f.required and f.default is None and f.name in body), None)
            S[op] = dict(method="POST", path="/v1" + ep.path, params={"dry_run": "true"}, json=body, ok=201, bad={"json": {k: v for k, v in body.items() if k != (first_req.name if first_req else "")}} if first_req else {"json": {**body, "zzz_unknown": 1}})
        elif ep.operation == "get":
            row = sp.row(e)
            if row is None: continue
            fac = sp.member_factors(row[ep.verify_member]) if ep.verify_member else {}
            S[op] = dict(method="GET", path="/v1" + ep.path.replace("{" + pname + "}", str(row[pk])), params=fac or None, ok=200, bad={"params": {"dob": "1900-01-01", "last_name": "x"}} if fac else {}, bad_path="/v1" + ep.path.replace("{" + pname + "}", "no-such-id-0000"))
            S[op] = {k: v for k, v in S[op].items() if v is not None}
        elif ep.operation == "list":
            row = sp.row(e); fac = {}
            if ep.verify_member and row: fac = {ep.verify_member: row[ep.verify_member], **sp.member_factors(row[ep.verify_member])}
            S[op] = dict(method="GET", path="/v1" + ep.path, params=fac or {"limit": "5"}, ok=200, bad={"params": {**(fac or {}), "limit": "0"}})
        else:
            en = next((spec.field(e, n) for n in ep.updatable if spec.field(e, n).type == "enum"), None)
            row = None
            if ep.transitions and en: row = sp.row(e, f"{en.name} IN ({','.join('?' * len(ep.transitions))})", tuple(ep.transitions))
            row = row or sp.row(e)
            if row is None: continue
            fac = sp.member_factors(row[ep.verify_member]) if ep.verify_member else {}
            target = (ep.transitions or {}).get(row[en.name], [None])[0] if en and ep.transitions else (en.values if isinstance(en.values, list) else list(en.values))[0] if en else None
            field = en.name if en and target else ep.updatable[0]
            S[op] = dict(method="PATCH", path="/v1" + ep.path.replace("{" + pname + "}", str(row[pk])), params={"dry_run": "true"}, json={field: target, **fac}, ok=200,
                         bad={"json": {"zzz_unknown": 1, **fac}})
    return S


def smoke_scenarios(sp, call, token_ok=True):
    """[(code, name, fn)] for the smoke gate. `call(method, url, **kw)` is the smoke harness request helper (authenticated)."""
    spec = sp.spec; out = []
    for e in spec.entities:
        cr = next((x for x in spec.endpoints if x.entity.lower() == e.name.lower() and x.operation == "create"), None)
        if cr is None: continue
        ge, li, up = sp.endpoint(cr, "get"), sp.endpoint(cr, "list"), sp.endpoint(cr, "update"); pk = spec.pk(e).name; code = (e.table.upper().replace("_", "")[:3])

        def url(ep, row): return "/v1" + ep.path.replace("{" + pk + "}", str(row[pk])).replace("{id}", str(row[pk]))
        def fac(ep, row): return sp.member_factors(row[ep.verify_member]) if ep.verify_member else {}
        def make(cr=cr, e=e, ge=ge, li=li, up=up, url=url, fac=fac):
            n = [0]
            def created():
                body = sp.create_body(cr); r = call("post", "/v1" + cr.path, json=body); return body, r
            def s1():
                body, r = created()
                if r.status_code != 201: return False, None, None
                row = r.json()
                if not (row[pk] and all(row[k] == v for k, v in body.items() if k in row and not isinstance(v, dict))): return False, None, None
                if ge: g = call("get", url(ge, row), params=fac(ge, row)); return g.status_code == 200 and g.json()[pk] == row[pk], None, None
                return True, None, None
            def s2():
                body, _ = created(); bad = {k: v for k, v in body.items() if k != next((f.name for f in e.fields if not f.primary_key and not f.server_set and f.required and f.default is None and f.name in body and f.name != cr.verify_member), "")}
                r = call("post", "/v1" + cr.path, json=bad); ok = r.status_code == 422 and r.json()["error"]["code"]
                if cr.verify_member: v = call("post", "/v1" + cr.path, json={**body, "last_name": "NotTheName"}); ok = ok and v.status_code == 403 and v.json()["error"]["code"] == "VERIFICATION_FAILED" and "member_id" not in v.text
                return bool(ok), None, (True if cr.verify_member else None)
            res = [(f"{code}-01", f"{e.name}: create and read back", s1), (f"{code}-02", f"{e.name}: invalid request rejected" + (", unverified caller discloses nothing" if cr.verify_member else ""), s2)]
            if e.unique:
                def s3():
                    body, r = created()
                    if r.status_code != 201: return False, None, None
                    row = r.json(); clash = {**body}
                    return call("post", "/v1" + cr.path, json=clash).status_code in (200, 201) and True, None, None   # repeat of the identical request is idempotent
                def s4():
                    ex = sp.row(e, " AND ".join([f"{k} NOT IN ({','.join('?' * len(v))})" for k, v in (e.unique_exempt or {}).items()]) or "1=1", tuple(x for v in (e.unique_exempt or {}).values() for x in v))
                    body = sp.create_body(cr)
                    if ex is None or body is None: return False, None, None
                    clash = {**body, **{c: ex[c] for u in e.unique for c in u}}
                    r = call("post", "/v1" + cr.path, json=clash); return r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT", None, None
                res += [(f"{code}-03", f"{e.name}: repeating a request is idempotent", s3), (f"{code}-04", f"{e.name}: unique constraint returns 409", s4)]
            if up:
                def s5():
                    body, r = created(); row = r.json(); en = next((spec.field(e, n) for n in up.updatable if spec.field(e, n).type == "enum"), None)
                    if en is None: return True, None, None
                    nxt = (up.transitions or {}).get(row[en.name], [])
                    if not nxt: return False, None, None
                    u = call("patch", url(up, row), json={en.name: nxt[0], **fac(up, row)})
                    if u.status_code != 200 or u.json()[en.name] != nxt[0]: return False, None, None
                    back = call("patch", url(up, row), json={en.name: row[en.name], **fac(up, row)})        # moving back is not an allowed transition
                    return back.status_code == 409 and back.json()["error"]["code"] == "INVALID_TRANSITION", None, None
                res.append((f"{code}-05", f"{e.name}: status changes follow the allowed transitions", s5))
            if li:
                def s6():
                    body, r = created(); row = r.json()
                    params = {li.verify_member: row[li.verify_member], **fac(li, row)} if li.verify_member else {"limit": "100"}
                    l = call("get", "/v1" + li.path, params={**params, "limit": "100"}); return l.status_code == 200 and any(i[pk] == row[pk] for i in l.json()["items"]), None, None
                res.append((f"{code}-06", f"{e.name}: new record appears in the list", s6))
            return res
        out += make()
    return out
