"""Generic, deterministic record generator driven by the entity definitions (no per-entity code).

Every value is a pure function of (root_seed, "sx", table, row index [, retry]) so a replay reproduces the rows exactly. Parent keys are read back from the
database in row order, so references always point at rows that exist (rule R-01 checks it)."""
import re
from .. import refdata as R
from ..common import Rng
from .spec import snake


def schema_tables(spec):
    """Table definitions in the shape schema.register_custom wants."""
    out = {}
    for e in spec.entities:
        cols = [(f.name, {"id": "T", "string": "T", "text": "T", "int": "I", "float": "R", "bool": "B", "date": "T", "datetime": "T", "enum": "T", "json": "J"}.get(f.type) or spec.ref_letter(f)) for f in e.fields]
        out[e.table] = {"pk": [spec.pk(e).name], "cols": cols, "fks": {f.name: f.ref for f in e.fields if f.type == "ref"}}
    return out


def _pattern(p, r, i):
    def sub(m):
        kind, n = m.group(1), int(m.group(2))
        if kind == "seq": return str(i).zfill(n)
        if kind == "digits": return "".join(str(r.i(0, 9)) for _ in range(n))
        return "".join(r.pick("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(n))
    return re.sub(r"\{(seq|digits|alnum):(\d+)\}", sub, p)


def _named(g, r):
    if g == "first_name": return r.pick(R.FIRST_F + R.FIRST_M)
    if g == "last_name": return r.pick(R.LAST)
    if g == "full_name": return r.pick(R.FIRST_F + R.FIRST_M) + " " + r.pick(R.LAST)
    if g == "city": return r.pick(R.CITIES)[0]
    if g == "state": return r.pick(R.CITIES)[1]
    if g == "phone": return f"555-01{r.i(0, 99):02d}"                      # reserved fictional range
    if g == "email": return f"user{r.i(1000, 999999)}@example.org"         # reserved example domain
    return r.pick(["Routine follow-up visit", "Needs records before the visit", "Member requested a morning slot", "Interpreter requested", "Transportation arranged"])


def _enum_values(f): return list(f.values) if isinstance(f.values, list) else list(f.values)
def _pick_enum(f, r):
    if isinstance(f.values, list): return r.pick(f.values)
    return r.weighted(sorted(f.values.items()))


def value(spec, ctx, e, f, r, i, row, keys, parent_val=None):
    """One field value. keys = {ref target: [pk values in row order]}."""
    if f.null_rate and r.chance(f.null_rate): return None
    t = f.type
    if t == "id": return ctx.id(e.table, i)
    if t == "ref":
        if parent_val is not None: return parent_val
        k = keys[f.ref]
        if not k: raise ValueError(f"{e.name}.{f.name}: {f.ref} has no rows to reference")
        return k[int(r.u() * len(k))]
    if t == "enum": return _pick_enum(f, r)
    if t == "bool": return r.chance(0.5 if f.p is None else f.p)
    if t in ("int", "float"):
        lo = 0 if f.min is None else f.min; hi = (lo + 100) if f.max is None else f.max
        if f.mean is not None:
            sd = f.sd if f.sd is not None else max(1e-9, (hi - lo) / 6); v = min(hi, max(lo, r.gauss(f.mean, sd)))
        else: v = lo + r.u() * (hi - lo)
        return int(round(v)) if t == "int" else round(v, 2)
    if t in ("date", "datetime"):
        lo, hi = f.offset_days or [-30, 30]; days = r.i(lo, hi)
        if f.after:
            base = row[f.after]
            if base is None: return None
            from datetime import date, timedelta
            d = date.fromisoformat(str(base)[:10]) + timedelta(days=days); return d.isoformat() if t == "date" else d.isoformat() + f"T{r.i(8, 17):02d}:{r.pick([0, 15, 30, 45]):02d}:00Z"
        return ctx.d(days) if t == "date" else ctx.ts(days=days, hours=0).replace("12:00:00", f"{r.i(8, 17):02d}:{r.pick([0, 15, 30, 45]):02d}:00")
    if t == "json": return {}
    if f.generator: return _named(f.generator, r)
    if f.pattern: return _pattern(f.pattern, r, i)
    if f.primary_key: return f"{snake(e.name).upper()[:4]}-{i:08d}"
    return _named("sentence", r)


def generate(spec, ctx, sink, con, log=lambda *_: None):
    """Emit every custom entity in dependency order. Returns {table: rows}."""
    counts = {}; key_cache = {}

    def keys_of(target):
        if target not in key_cache:
            t, c = target.split("."); sink.flush()
            key_cache[target] = [x[0] for x in con.execute(f"SELECT {c} FROM {t} ORDER BY row_idx")]
        return key_cache[target]

    for e in spec.entities:
        key_cache.pop(f"{e.table}.{spec.pk(e).name}", None)
        refs = {f.ref for f in e.fields if f.type == "ref"}; keys = {k: keys_of(k) for k in refs}
        c = e.count; plan = []          # plan: list of (row index, parent value or None, parent ref field)
        if c.fixed is not None: plan = [(i, None) for i in range(c.fixed)]
        elif c.ratio is not None:
            n = max(1, int(round(c.ratio["value"] * len(keys_of_table(con, sink, c.ratio["of"]))))); plan = [(i, None) for i in range(n)]
        else:
            of = c.per_parent["of"]; pf = next(f for f in e.fields if f.type == "ref" and f.ref.split(".")[0] == of)
            parents = keys_of(pf.ref); lo, hi = c.per_parent.get("min", 1), c.per_parent.get("max", 1); i = 0
            for pi, pv in enumerate(parents):
                for _ in range(ctx.rng("sx", e.table + ".count", pi).i(lo, hi)): plan.append((i, (pf.name, pv))); i += 1
        seen = {tuple(u): set() for u in map(tuple, e.unique)}
        for i, parent in plan:
            for attempt in range(60):
                r = ctx.rng("sx", e.table, i if attempt == 0 else f"{i}:{attempt}"); row = {}
                for f in e.fields: row[f.name] = value(spec, ctx, e, f, r, i, row, keys, parent[1] if parent and parent[0] == f.name else None)
                ks = {u: tuple(row[n] for n in u) for u in seen}
                if all(ks[u] not in seen[u] for u in seen): break
            else: raise ValueError(f"{e.name}: could not satisfy unique {e.unique} for row {i}; widen the ranges")
            for u in seen: seen[u].add(ks[u])
            sink.emit(e.table, i, e.scenario_id, **row)
        sink.flush(e.table); counts[e.table] = len(plan); log(f"  custom entity {e.name}: {len(plan):,} rows")
    return counts


def keys_of_table(con, sink, table):
    sink.flush(); return [x[0] for x in con.execute(f"SELECT row_idx FROM {table}")]
