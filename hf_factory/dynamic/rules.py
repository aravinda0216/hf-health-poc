"""Validation rules generated from the entity definitions (codes X-01..X-06). Foreign keys are already covered by R-01 because custom FKs are registered in schema.FKS."""


def add(rule, con, spec):
    q1 = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    agg = {k: [0, 0, []] for k in ("X-01", "X-02", "X-03", "X-04", "X-05", "X-06")}      # code -> [checked, bad, samples]
    def hit(code, checked, bad, label):
        agg[code][0] += checked; agg[code][1] += bad
        if bad: agg[code][2].append(f"{label}: {bad}")
    for e in spec.entities:
        t, pk = e.table, spec.pk(e).name; n = q1(f"SELECT COUNT(*) FROM {t}")
        hit("X-01", n, q1(f"SELECT COUNT(*) - COUNT(DISTINCT {pk}) FROM {t}") + q1(f"SELECT COUNT(*) FROM {t} WHERE {pk} IS NULL"), f"{t}.{pk}")
        for f in e.fields:
            if f.required and not f.null_rate: hit("X-02", n, q1(f"SELECT COUNT(*) FROM {t} WHERE {f.name} IS NULL"), f"{t}.{f.name}")
            if f.type == "enum":
                vals = list(f.values); ph = ",".join("?" * len(vals))
                hit("X-03", n, q1(f"SELECT COUNT(*) FROM {t} WHERE {f.name} IS NOT NULL AND {f.name} NOT IN ({ph})", *vals), f"{t}.{f.name}")
            if f.type in ("int", "float") and (f.min is not None or f.max is not None):
                lo = f.min if f.min is not None else -1e18; hi = f.max if f.max is not None else 1e18
                hit("X-04", n, q1(f"SELECT COUNT(*) FROM {t} WHERE {f.name} IS NOT NULL AND ({f.name} < ? OR {f.name} > ?)", lo, hi), f"{t}.{f.name}")
            if f.after: hit("X-06", n, q1(f"SELECT COUNT(*) FROM {t} WHERE {f.name} IS NOT NULL AND {f.name} < {f.after}"), f"{t}.{f.name} before {f.after}")
        for u in e.unique:
            cols = ",".join(u); hit("X-05", n, q1(f"SELECT COUNT(*) FROM (SELECT 1 FROM {t} GROUP BY {cols} HAVING COUNT(*) > 1)"), f"{t} unique({cols})")
    desc = {"X-01": "Custom entities: primary keys unique and not null", "X-02": "Custom entities: required fields not null", "X-03": "Custom entities: enum values are declared values",
            "X-04": "Custom entities: numeric fields inside min/max", "X-05": "Custom entities: unique constraints hold", "X-06": "Custom entities: 'after' dates are not before their anchor date"}
    for code, (c, b, s) in agg.items(): rule(code, desc[code], c, b, s)
