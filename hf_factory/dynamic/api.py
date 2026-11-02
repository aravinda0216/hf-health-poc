"""Routes generated from api_spec.custom_endpoints. One factory per operation (create, get, list, update); nothing is written per entity.

Rules shared by every generated route
  * bearer token required (same dependency as the built-in routes); errors use the standard error body; every response is flagged synthetic
  * writes go to the STATE database (table dyn_<table>), never the dataset; a dataset row that is updated is copied into the state table and read from there afterwards
  * create: typed and range-checked body, references must exist, unique constraints return 409, `dry_run` validates without saving, ids are deterministic so a repeat is idempotent
  * verify_member: the caller must give the referenced member's date of birth and last name; any mismatch is the same 403 and discloses nothing
  * update: only the declared updatable fields; enum changes must follow the declared transitions (409 otherwise)
"""
import hashlib, inspect, json, re, uuid
from datetime import date
from typing import Any, List, Literal, Optional
from fastapi import Query
from pydantic import ConfigDict, Field, create_model
from ..api import models as M
from .spec import camel

SQL = {"T": "TEXT", "I": "INTEGER", "R": "REAL", "B": "INTEGER", "J": "TEXT"}
LETTER = {"id": "T", "string": "T", "text": "T", "int": "I", "float": "R", "bool": "B", "date": "T", "datetime": "T", "enum": "T", "json": "J"}
VERIFY_FAIL = "The member could not be verified. No information was disclosed."


def _letter(spec, f): return LETTER.get(f.type) or spec.ref_letter(f)


def _out_type(spec, f):
    L = _letter(spec, f)
    return {"I": int, "R": float, "B": bool, "J": dict}.get(L, str)


def _in_type(spec, f):
    if f.type == "enum": return Literal[tuple(f.values)]
    if f.type == "date": return date
    if f.type == "json": return dict
    return _out_type(spec, f)


def _constraints(f):
    k = {}
    if f.type in ("int", "float"):
        if f.min is not None: k["ge"] = f.min
        if f.max is not None: k["le"] = f.max
    if f.type in ("string", "text"):
        k["max_length"] = int(f.max) if f.max else (2000 if f.type == "text" else 200)
        if f.min: k["min_length"] = int(f.min)
    return k


def register(router, spec, ns):
    """Mount the generated routes on `router`. ns supplies con_ro, state, lock, prot, ERR, GUARD, ApiError."""
    ApiError = ns.ApiError; models = {}

    def tinfo(e):
        pk = spec.pk(e); return pk, {f.name: f for f in e.fields}

    # ---------- response / request models (one set per entity)
    for e in spec.entities:
        n = camel(e.name); pk = spec.pk(e)
        out_fields = {f.name: (Optional[_out_type(spec, f)], Field(None, description=f.description)) for f in e.fields}
        item = create_model(n, __base__=M.Envelope, **out_fields)
        lst = create_model(n + "List", __base__=M.Envelope, items=(List[item], ...), count=(int, ...), total=(int, ...), limit=(int, ...), offset=(int, ...))
        in_fields = {}
        for f in e.fields:
            if f.primary_key or f.server_set: continue
            req = f.required and f.default is None
            in_fields[f.name] = (_in_type(spec, f) if req else Optional[_in_type(spec, f)], Field(... if req else f.default, description=f.description, **_constraints(f)))
        models[e.table] = dict(item=item, list=lst, in_fields=in_fields)
        item_cls = item
        item_cls.__doc__ = e.description or n

    def state_table(e):
        return "dyn_" + e.table

    def ensure(s, e):
        cols = ", ".join(f"{f.name} {SQL[_letter(spec, f)]}" for f in e.fields)
        s.execute(f"CREATE TABLE IF NOT EXISTS {state_table(e)} ({cols}, _origin TEXT, PRIMARY KEY ({spec.pk(e).name}))")

    def py(e, row):
        """DB row -> API dict (bool / json restored)."""
        out = {}
        for f in e.fields:
            v = row[f.name]; L = _letter(spec, f)
            out[f.name] = (None if v is None else bool(v) if L == "B" else json.loads(v) if L == "J" and isinstance(v, str) else v)
        return out

    def store(e, v, f):
        L = _letter(spec, f)
        if v is None: return None
        if L == "B": return int(bool(v))
        if L == "J": return json.dumps(v, separators=(",", ":"))
        return v.isoformat() if isinstance(v, date) else v

    def find(e, pkv):
        """State row first (it overrides the dataset), then the dataset. Returns (row dict, origin) or (None, None)."""
        pk = spec.pk(e).name; s = ns.state()
        try:
            ensure(s, e); r = s.execute(f"SELECT * FROM {state_table(e)} WHERE {pk}=?", (pkv,)).fetchone()
        finally: s.close()
        if r is not None: return py(e, r), "state"
        r = ns.con_ro().execute(f"SELECT * FROM {e.table} WHERE {pk}=?", (pkv,)).fetchone()
        return (py(e, r), "dataset") if r is not None else (None, None)

    def exists(target, value):
        t, c = target.split(".")
        if ns.con_ro().execute(f"SELECT 1 FROM {t} WHERE {c}=? LIMIT 1", (value,)).fetchone(): return True
        ce = spec.entity(t)
        if ce is not None:
            s = ns.state()
            try:
                ensure(s, ce); return s.execute(f"SELECT 1 FROM {state_table(ce)} WHERE {c}=? LIMIT 1", (value,)).fetchone() is not None
            finally: s.close()
        return False

    def verify(member_id, dob, last_name):
        r = ns.con_ro().execute("SELECT dob, last_name FROM member_demographics WHERE member_id=?", (member_id,)).fetchone()
        if r is None or str(dob) != r["dob"] or (last_name or "").strip().casefold() != (r["last_name"] or "").casefold():
            raise ApiError(403, "VERIFICATION_FAILED", VERIFY_FAIL)

    def clashes(e, row, ignore_pk=None):
        """Unique constraints against the dataset and the state table. Rows whose exempt field has an exempt value (for example CANCELLED) do not hold the slot."""
        pk = spec.pk(e).name; ex = e.unique_exempt or {}
        for u in e.unique:
            where = " AND ".join(f"{c}=?" for c in u); args = [row[c] for c in u]
            for src, conn in (("dataset", ns.con_ro()), ("state", None)):
                sql_ex = "".join(f" AND {k} NOT IN ({','.join('?' * len(v))})" for k, v in ex.items()); a2 = args + [x for v in ex.values() for x in v]
                if ignore_pk is not None: sql_ex += f" AND {pk}!=?"; a2.append(ignore_pk)
                if conn is not None:
                    hit = conn.execute(f"SELECT {pk} FROM {e.table} WHERE {where}{sql_ex}", a2).fetchone()
                    if hit:        # a dataset row that was later changed in state holds the slot only if its state copy still clashes (checked in the state branch)
                        s = ns.state()
                        try:
                            ensure(s, e); over = s.execute(f"SELECT 1 FROM {state_table(e)} WHERE {pk}=?", (hit[0],)).fetchone()
                        finally: s.close()
                        if not over: return u
                else:
                    s = ns.state()
                    try:
                        ensure(s, e)
                        if s.execute(f"SELECT {pk} FROM {state_table(e)} WHERE {where}{sql_ex}", a2).fetchone(): return u
                    finally: s.close()
        return None

    def new_pk(e, body):
        pk = spec.pk(e)
        digest = hashlib.sha256(json.dumps({k: str(v) for k, v in sorted(body.items())}, sort_keys=True).encode()).hexdigest()
        if pk.type == "id": return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hfactory-api:{e.table}:{digest}"))
        if pk.type == "string":
            def sub(m):
                kind, n = m.group(1), int(m.group(2)); h = int(digest, 16)
                return str(h % 10**n).zfill(n) if kind in ("seq", "digits") else digest[:n].upper()
            return re.sub(r"\{(seq|digits|alnum):(\d+)\}", sub, pk.pattern) if pk.pattern else f"{e.table[:4].upper()}-{digest[:10].upper()}"
        s = ns.state()
        try:
            ensure(s, e); a = s.execute(f"SELECT COALESCE(MAX({pk.name}),0) FROM {state_table(e)}").fetchone()[0]
        finally: s.close()
        return max(a, ns.con_ro().execute(f"SELECT COALESCE(MAX({pk.name}),0) FROM {e.table}").fetchone()[0]) + 1

    def guard(ep, e, verify_on):
        never = list(ep.never) + (["Act for a member who has not been verified (date of birth and last name)"] if ep.verify_member else [])
        return {"x-agent-tool": {"when": ep.when or f"Use to {(ep.summary or ep.operation).lower()}.", "escalate": list(ep.escalate), "never": never}}

    # ---------- operation factories
    def make_create(ep, e):
        m = models[e.table]; pk = spec.pk(e).name
        fields = dict(m["in_fields"])
        if ep.verify_member:
            fields["dob"] = (date, Field(..., description="Member date of birth (verification)")); fields["last_name"] = (str, Field(..., min_length=1, max_length=60, description="Member last name (verification)"))
        Body = create_model(camel(e.name) + "Create", __config__=ConfigDict(extra="forbid"), **fields)

        def handler(body, dry_run=False):
            d = body.model_dump()
            if ep.verify_member: verify(d[ep.verify_member], d.pop("dob"), d.pop("last_name"))
            for f in e.fields:
                if f.type == "ref" and d.get(f.name) is not None and not exists(f.ref, d[f.name]):
                    raise ApiError(404, "REFERENCE_NOT_FOUND", f"{f.name} does not match any known {f.ref.split('.')[0]}.", {"field": f.name}, e.scenario_id)
            row = {}
            for f in e.fields:
                if f.primary_key: continue
                v = f.default if (f.server_set or d.get(f.name) is None) else d[f.name]      # server-set fields always take their default
                row[f.name] = v.isoformat() if isinstance(v, date) else v
            with ns.lock:
                # Deterministic id from the content: repeating an identical request returns the same record (idempotent). If that record has since moved on
                # (for example it was cancelled), the same request is a new booking and gets the next id.
                for attempt in range(50):
                    row[pk] = new_pk(e, {**{k: v for k, v in row.items() if k != pk}, "_n": attempt})
                    ex, origin = find(e, row[pk])
                    if ex is None or all(ex[f.name] == (f.default if f.server_set else row[f.name]) for f in e.fields if not f.primary_key): break
                if ex is not None and all(ex[f.name] == (f.default if f.server_set else row[f.name]) for f in e.fields if not f.primary_key): out = ex
                else:
                    u = clashes(e, row)
                    if u: raise ApiError(409, "CONFLICT", f"{e.name} already exists for the same {', '.join(u)}.", {"conflicts_on": u}, e.scenario_id)
                    out = row
                    if not dry_run:
                        s = ns.state()
                        try:
                            ensure(s, e); cols = [f.name for f in e.fields]
                            s.execute(f"INSERT INTO {state_table(e)} ({','.join(cols)},_origin) VALUES ({','.join('?' * (len(cols) + 1))})", [store(e, row[c], spec.field(e, c)) for c in cols] + ["created"]); s.commit()
                        finally: s.close()
            out = {k: v for k, v in out.items() if k != "_idem"}
            return m["item"](**out, scenario_id=e.scenario_id)
        sig = [inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Body), inspect.Parameter("dry_run", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=Query(False, description="Validate and preview without saving"), annotation=bool)]
        return handler, sig, 201

    def verify_query_params(ep):
        return [inspect.Parameter("dob", inspect.Parameter.KEYWORD_ONLY, default=Query(..., description="Member date of birth (verification)"), annotation=date),
                inspect.Parameter("last_name", inspect.Parameter.KEYWORD_ONLY, default=Query(..., min_length=1, max_length=60, description="Member last name (verification)"), annotation=str)] if ep.verify_member else []

    def path_param(ep, e):
        pk = spec.pk(e).name; name = pk if "{" + pk + "}" in ep.path else "id"
        return name, inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str if _out_type(spec, spec.pk(e)) is str else int)

    def make_get(ep, e):
        m = models[e.table]; pname, pparam = path_param(ep, e)

        def handler(**kw):
            row, _ = find(e, kw[pname])
            if row is None: raise ApiError(404, "NOT_FOUND", f"{e.name} not found.", None, e.scenario_id)
            if ep.verify_member: verify(row[ep.verify_member], kw["dob"], kw["last_name"])
            return m["item"](**row, scenario_id=e.scenario_id)
        return handler, [pparam] + [p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in verify_query_params(ep)], 200

    def make_list(ep, e):
        m = models[e.table]; pk = spec.pk(e).name; flt = {n: spec.field(e, n) for n in ep.filters}

        def handler(**kw):
            if ep.verify_member: verify(kw[ep.verify_member], kw["dob"], kw["last_name"])
            limit, offset = kw["limit"], kw["offset"]; conds, args = [], []
            for n, f in flt.items():
                v = kw.get(n)
                if v is not None: conds.append(f"{n}=?"); args.append(v.isoformat() if isinstance(v, date) else v)
            where = (" WHERE " + " AND ".join(conds)) if conds else ""
            s = ns.state()
            try:
                ensure(s, e); srows = [py(e, r) for r in s.execute(f"SELECT * FROM {state_table(e)}{where}", args).fetchall()]
                skeys = [r[0] for r in s.execute(f"SELECT {pk} FROM {state_table(e)}").fetchall()]
            finally: s.close()
            excl = f"{pk} NOT IN ({','.join('?' * len(skeys))})" if skeys else ""
            dw = " AND ".join(x for x in ([" AND ".join(conds)] if conds else []) + ([excl] if excl else [])); dw = (" WHERE " + dw) if dw else ""
            da = args + skeys; c = ns.con_ro()
            total = c.execute(f"SELECT COUNT(*) FROM {e.table}{dw}", da).fetchone()[0] + len(srows)
            take = max(0, limit - max(0, len(srows) - offset)); soff = max(0, offset - len(srows))
            part = srows[offset:offset + limit]
            drows = [py(e, r) for r in c.execute(f"SELECT * FROM {e.table}{dw} ORDER BY row_idx LIMIT ? OFFSET ?", da + [take, soff]).fetchall()] if take else []
            items = [m["item"](**r, scenario_id=e.scenario_id) for r in part + drows]
            return m["list"](items=items, count=len(items), total=total, limit=limit, offset=offset, scenario_id=e.scenario_id)
        params = []
        for n, f in flt.items():
            t = date if f.type == "date" else (Literal[tuple(f.values)] if f.type == "enum" else _out_type(spec, f))
            need = ep.verify_member == n
            params.append(inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, default=Query(... if need else None, description=f"Filter by {n}"), annotation=t if need else Optional[t]))
        if ep.verify_member and ep.verify_member not in flt: raise ValueError(f"{ep.path}: verify_member field {ep.verify_member} must be in filters")
        params += verify_query_params(ep)
        params += [inspect.Parameter("limit", inspect.Parameter.KEYWORD_ONLY, default=Query(20, ge=1, le=100), annotation=int), inspect.Parameter("offset", inspect.Parameter.KEYWORD_ONLY, default=Query(0, ge=0), annotation=int)]
        return handler, params, 200

    def make_update(ep, e):
        m = models[e.table]; pname, pparam = path_param(ep, e); pk = spec.pk(e).name
        fields = {}
        for n in ep.updatable:
            f = spec.field(e, n); fields[n] = (Optional[_in_type(spec, f)], Field(None, description=f.description, **_constraints(f)))
        if ep.verify_member:
            fields["dob"] = (date, Field(..., description="Member date of birth (verification)")); fields["last_name"] = (str, Field(..., min_length=1, max_length=60, description="Member last name (verification)"))
        Body = create_model(camel(e.name) + "Update", __config__=ConfigDict(extra="forbid"), **fields)
        enum_f = next((spec.field(e, n) for n in ep.updatable if spec.field(e, n).type == "enum"), None)

        def handler(body, dry_run=False, **kw):
            row, origin = find(e, kw[pname])
            if row is None: raise ApiError(404, "NOT_FOUND", f"{e.name} not found.", None, e.scenario_id)
            d = body.model_dump(exclude_unset=True)
            if ep.verify_member: verify(row[ep.verify_member], d.pop("dob"), d.pop("last_name"))
            d = {k: v for k, v in d.items() if v is not None}
            if not d: raise ApiError(422, "NO_CHANGES", "Provide at least one field to change.", {"updatable": ep.updatable}, e.scenario_id)
            if ep.transitions and enum_f and enum_f.name in d:
                cur, new = row[enum_f.name], d[enum_f.name]
                if new != cur and new not in ep.transitions.get(cur, []):
                    raise ApiError(409, "INVALID_TRANSITION", f"{enum_f.name} cannot change from {cur} to {new}.", {"from": cur, "allowed": ep.transitions.get(cur, [])}, e.scenario_id)
            new_row = {**row, **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}}
            with ns.lock:
                u = clashes(e, new_row, ignore_pk=row[pk]) if any(k in {c for u in e.unique for c in u} for k in d) else None
                if u: raise ApiError(409, "CONFLICT", f"{e.name} already exists for the same {', '.join(u)}.", {"conflicts_on": u}, e.scenario_id)
                if not dry_run:
                    s = ns.state()
                    try:
                        ensure(s, e); cols = [f.name for f in e.fields]
                        s.execute(f"INSERT OR REPLACE INTO {state_table(e)} ({','.join(cols)},_origin) VALUES ({','.join('?' * (len(cols) + 1))})", [store(e, new_row[c], spec.field(e, c)) for c in cols] + [origin if origin == 'state' else 'dataset']); s.commit()
                    finally: s.close()
            return m["item"](**new_row, scenario_id=e.scenario_id)
        sig = [pparam, inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Body), inspect.Parameter("dry_run", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=Query(False, description="Validate and preview without saving"), annotation=bool)]
        return handler, sig, 200

    FACT = {"create": make_create, "get": make_get, "list": make_list, "update": make_update}
    registered = []
    for ep in spec.endpoints:
        e = spec.entity(ep.entity); impl, sig, status = FACT[ep.operation](ep, e)
        # Update handlers take the path param plus body; wrap so FastAPI sees the generated signature.
        if ep.operation == "create": fn = (lambda impl: (lambda body, dry_run=False: impl(body, dry_run)))(impl)
        elif ep.operation == "update": fn = (lambda impl, pname: (lambda **kw: impl(kw.pop("body"), kw.pop("dry_run"), **kw)))(impl, path_param(ep, e)[0])
        else: fn = (lambda impl: (lambda **kw: impl(**kw)))(impl)
        fn.__signature__ = inspect.Signature(sig); fn.__name__ = spec.op_id(ep)
        resp = models[e.table]["list"] if ep.operation == "list" else models[e.table]["item"]
        router.add_api_route(ep.path, fn, methods=[ep.method], response_model=resp, status_code=status, dependencies=ns.prot, tags=["custom-" + e.table], operation_id=spec.op_id(ep),
                             responses=ns.ERR | ({409: {"model": M.ErrorResponse}} if ep.operation in ("create", "update") else {}), summary=ep.summary or f"{ep.operation} {e.name}", openapi_extra=guard(ep, e, bool(ep.verify_member)))
        registered.append(spec.op_id(ep))
    return registered
