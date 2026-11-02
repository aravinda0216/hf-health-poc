"""Schema and validation for blueprint-defined entities (data_spec.custom_entities) and endpoints (api_spec.custom_endpoints).

Entity:  {"name": "Appointment", "count": {"fixed": 20000},
          "fields": [{"name": "appointment_id", "type": "id", "primary_key": true},
                     {"name": "member_id", "type": "ref", "ref": "member_demographics.member_id"},
                     {"name": "provider_id", "type": "ref", "ref": "provider.npi"},
                     {"name": "appointment_date", "type": "date", "offset_days": [-30, 60]},
                     {"name": "status", "type": "enum", "values": {"BOOKED": 70, "COMPLETED": 20, "CANCELLED": 10}, "default": "BOOKED", "server_set": true}],
          "unique": [["provider_id", "appointment_date"]]}
Endpoint: {"method": "POST", "path": "/appointments", "entity": "Appointment", "operation": "create"}   # create | get | list | update

Field types: id (UUID, generated), string, text, int, float, bool, date, datetime, enum, ref (foreign key), json.
Everything is validated up front; a bad definition fails with a SpecError that names the entity and field.
"""
import re
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field as PField, ValidationError, field_validator

Num = Union[int, float]
FIELD_TYPES = ("id", "string", "text", "int", "float", "bool", "date", "datetime", "enum", "ref", "json")
GENERATORS = ("first_name", "last_name", "full_name", "city", "state", "phone", "email", "sentence")
RESERVED_COLS = {"row_idx", "scenario_id", "tenant_id", "is_synthetic"}
IDENT = r"^[a-z][a-z0-9_]{0,40}$"
OPERATIONS = ("create", "get", "list", "update")
LETTER = {"id": "T", "string": "T", "text": "T", "int": "I", "float": "R", "bool": "B", "date": "T", "datetime": "T", "enum": "T", "ref": "T", "json": "J"}


class SpecError(ValueError):
    pass


def snake(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())).strip("_").lower()
    return s


def camel(name: str) -> str:
    return "".join(p.capitalize() for p in snake(name).split("_"))


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = PField(..., pattern=IDENT)
    type: Literal[FIELD_TYPES]
    primary_key: bool = False
    required: bool = True
    description: Optional[str] = None
    ref: Optional[str] = None                        # "table.column": a core table or another custom entity's table
    values: Optional[Union[List[str], Dict[str, Num]]] = None   # enum: list (uniform) or {value: weight}
    min: Optional[Num] = None; max: Optional[Num] = None        # int / float range (inclusive); string length bounds in the API
    mean: Optional[Num] = None; sd: Optional[Num] = None        # int / float normal distribution (clamped to min / max)
    pattern: Optional[str] = None                    # string: "APT-{seq:6}" -> APT-000042; tokens {seq:N} {digits:N} {alnum:N}
    generator: Optional[Literal[GENERATORS]] = None  # string: named synthetic generator
    offset_days: Optional[List[int]] = None          # date / datetime: [lo, hi] days from the dataset's as-of date (negative = past)
    after: Optional[str] = None                      # date / datetime: relative to this earlier field instead of the as-of date
    p: Optional[float] = PField(None, ge=0, le=1)    # bool: probability of true
    null_rate: float = PField(0.0, ge=0, le=1)       # share of generated rows where the field is null (needs required: false)
    default: Optional[Any] = None                    # value used when the API caller does not supply the field
    server_set: bool = False                         # not accepted from API callers; always the default (e.g. status = BOOKED)

    @field_validator("offset_days")
    @classmethod
    def _od(cls, v):
        if v is not None and (len(v) != 2 or v[0] > v[1]): raise ValueError("offset_days must be [low, high] with low <= high")
        return v


class Count(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixed: Optional[int] = PField(None, ge=1, le=50_000_000)
    per_parent: Optional[Dict[str, Any]] = None      # {"of": "member_demographics", "min": 0, "max": 3}: children per parent row (the ref field to that parent is assigned, not drawn)
    ratio: Optional[Dict[str, Any]] = None           # {"of": "claim_header", "value": 0.2}: value x the parent's row count


class EntityDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = PField(..., min_length=2, max_length=60)
    table: Optional[str] = PField(None, pattern=IDENT)
    description: Optional[str] = None
    count: Count
    fields: List[FieldDef] = PField(..., min_length=2)
    unique: List[List[str]] = PField(default_factory=list)      # composite uniqueness (generation retries; the API returns 409 on a clash)
    unique_exempt: Optional[Dict[str, List[str]]] = None        # {"status": ["CANCELLED"]}: rows with these values do not hold the unique slot in the API (a cancelled slot can be rebooked)
    scenario_id: str = "CUSTOM"


class EndpointDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST", "PATCH"]
    path: str = PField(..., pattern=r"^/[a-z0-9_\-/{}]*$")
    entity: str
    operation: Literal[OPERATIONS]
    operation_id: Optional[str] = None
    summary: Optional[str] = None
    filters: List[str] = PField(default_factory=list)           # list: query filters (field names)
    updatable: List[str] = PField(default_factory=list)         # update: fields the caller may change
    transitions: Optional[Dict[str, List[str]]] = None          # update: allowed value changes for the first enum updatable field, e.g. {"BOOKED": ["CANCELLED"]}
    verify_member: Optional[str] = None                         # name of the field holding a member_demographics id: the caller must also give that member's dob + last_name
    when: Optional[str] = None; escalate: List[str] = PField(default_factory=list); never: List[str] = PField(default_factory=list)   # agent-tool guardrails


class CustomSpec:
    """Validated, resolved form of the custom entities and endpoints. `entities` are in dependency order (parents first)."""
    def __init__(self, entities: List[EntityDef], endpoints: List[EndpointDef], core_cols: Dict[str, Dict[str, str]]):
        self.core = core_cols; self.entities: List[EntityDef] = []; self.endpoints = endpoints
        self._by_name = {e.name.lower(): e for e in entities}; self._by_table = {}
        for e in entities: e.table = e.table or snake(e.name); self._by_table[e.table] = e
        self.entities = self._order(entities)

    # ---- lookups
    def entity(self, name): return self._by_name.get(name.lower()) or self._by_table.get(name)
    def pk(self, e): return next(f for f in e.fields if f.primary_key)
    def field(self, e, name): return next((f for f in e.fields if f.name == name), None)
    def table_cols(self):
        """{table: {col: type letter}} for the custom tables."""
        return {e.table: {f.name: LETTER[f.type] for f in e.fields} for e in self.entities}

    def ref_target(self, f):
        t, c = f.ref.split("."); return t, c

    def ref_letter(self, f):
        t, c = self.ref_target(f)
        if t in self._by_table: return LETTER[self.field(self._by_table[t], c).type]
        return self.core[t][c]

    def _order(self, ents):
        deps = {e.table: {self.ref_target(f)[0] for f in e.fields if f.ref and self.ref_target(f)[0] in self._by_table and self.ref_target(f)[0] != e.table} for e in ents}
        out, done = [], set()
        while len(out) < len(ents):
            ready = [e for e in ents if e.table not in done and deps[e.table] <= done]
            if not ready: raise SpecError("custom entities reference each other in a cycle: " + ", ".join(sorted(t for t in deps if t not in done)))
            for e in sorted(ready, key=lambda e: e.table): out.append(e); done.add(e.table)
        return out

    def op_id(self, ep):
        if ep.operation_id: return ep.operation_id
        e = self.entity(ep.entity); n = camel(e.name)
        return {"create": f"create{n}", "get": f"get{n}", "list": f"list{n}s", "update": f"update{n}"}[ep.operation]

    def op_ids(self): return {self.op_id(ep) for ep in self.endpoints}


def parse_spec(entities: Optional[list], endpoints: Optional[list], core_tables: Dict[str, Dict[str, str]], existing_paths=()) -> Optional[CustomSpec]:
    """Validate raw blueprint dicts. core_tables = {table: {col: type letter}} of the built-in schema. Returns None when nothing is defined."""
    if not entities and not endpoints: return None
    try:
        ents = [EntityDef.model_validate(e) for e in (entities or [])]; eps = [EndpointDef.model_validate(e) for e in (endpoints or [])]
    except ValidationError as ex:
        raise SpecError("custom definition invalid: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in ex.errors()[:6]))
    if eps and not ents: raise SpecError("custom_endpoints need custom_entities")
    names, tables = set(), set()
    for e in ents:
        e.table = e.table or snake(e.name)
        if not re.match(IDENT, e.table): raise SpecError(f"{e.name}: table name {e.table!r} is not a valid identifier")
        if e.table in core_tables or e.table.startswith("_") or e.table == "sqlite_sequence": raise SpecError(f"{e.name}: table {e.table!r} collides with a built-in table")
        if e.table in tables or e.name.lower() in names: raise SpecError(f"duplicate entity {e.name}")
        names.add(e.name.lower()); tables.add(e.table)
    for e in ents:
        pks = [f for f in e.fields if f.primary_key]
        if len(pks) != 1: raise SpecError(f"{e.name}: exactly one primary_key field is required (found {len(pks)})")
        fn = [f.name for f in e.fields]
        if len(set(fn)) != len(fn): raise SpecError(f"{e.name}: duplicate field names")
        for f in e.fields:
            w = f"{e.name}.{f.name}"
            if f.name in RESERVED_COLS: raise SpecError(f"{w}: {f.name!r} is reserved")
            if f.primary_key and f.type not in ("id", "string", "int"): raise SpecError(f"{w}: primary key must be id, string or int")
            if f.primary_key and (not f.required or f.null_rate): raise SpecError(f"{w}: primary key cannot be nullable")
            if f.type == "enum":
                if not f.values: raise SpecError(f"{w}: enum needs values")
                vals = list(f.values) if isinstance(f.values, list) else list(f.values.keys())
                if len(set(vals)) != len(vals): raise SpecError(f"{w}: duplicate enum values")
                if f.default is not None and f.default not in vals: raise SpecError(f"{w}: default {f.default!r} is not one of the enum values")
            if f.type == "ref":
                if not f.ref or f.ref.count(".") != 1: raise SpecError(f"{w}: ref must be 'table.column'")
                t, c = f.ref.split(".")
                if t in tables:
                    other = next(x for x in ents if x.table == t)
                    if not any(g.name == c and g.primary_key for g in other.fields): raise SpecError(f"{w}: {f.ref} is not the primary key of {other.name}")
                elif t not in core_tables or c not in core_tables[t]: raise SpecError(f"{w}: {f.ref} does not exist")
            elif f.ref: raise SpecError(f"{w}: ref is only valid on type ref")
            if f.type in ("int", "float") and f.min is not None and f.max is not None and f.min > f.max: raise SpecError(f"{w}: min > max")
            if f.type == "string" and f.pattern and not re.fullmatch(r"(?:[^{}]|\{(?:seq|digits|alnum):\d{1,2}\})*", f.pattern): raise SpecError(f"{w}: pattern tokens must be {{seq:N}}, {{digits:N}} or {{alnum:N}}")
            if f.after:
                if f.type not in ("date", "datetime"): raise SpecError(f"{w}: after is only valid on date or datetime")
                tgt = next((g for g in e.fields if g.name == f.after), None)
                if tgt is None or tgt.type not in ("date", "datetime"): raise SpecError(f"{w}: after must name an earlier date field")
                if fn.index(f.after) > fn.index(f.name): raise SpecError(f"{w}: after field {f.after} must be declared earlier")
            if f.null_rate and f.required: raise SpecError(f"{w}: null_rate needs required: false")
            if f.server_set and f.default is None and not f.primary_key and f.type != "id": raise SpecError(f"{w}: server_set fields need a default")
        for u in e.unique:
            for n in u:
                if n not in fn: raise SpecError(f"{e.name}: unique references unknown field {n}")
        if e.unique_exempt and any(k not in fn for k in e.unique_exempt): raise SpecError(f"{e.name}: unique_exempt references an unknown field")
        c = e.count; n_set = sum(x is not None for x in (c.fixed, c.per_parent, c.ratio))
        if n_set != 1: raise SpecError(f"{e.name}: count needs exactly one of fixed, per_parent, ratio")
        for kind in ("per_parent", "ratio"):
            cc = getattr(c, kind)
            if cc is None: continue
            of = cc.get("of")
            if of not in core_tables and of not in tables: raise SpecError(f"{e.name}: count.{kind}.of {of!r} is not a known table")
            if kind == "per_parent":
                lo, hi = cc.get("min", 1), cc.get("max", 1)
                if not (isinstance(lo, int) and isinstance(hi, int) and 0 <= lo <= hi): raise SpecError(f"{e.name}: per_parent needs integer 0 <= min <= max")
                if not any(f.type == "ref" and f.ref.split(".")[0] == of for f in e.fields): raise SpecError(f"{e.name}: per_parent of {of} needs a ref field to {of}")
            elif not (isinstance(cc.get("value"), (int, float)) and cc["value"] > 0): raise SpecError(f"{e.name}: ratio.value must be > 0")
    spec = CustomSpec(ents, eps, core_tables)
    seen = set()
    for ep in eps:
        e = spec.entity(ep.entity)
        if e is None: raise SpecError(f"endpoint {ep.method} {ep.path}: unknown entity {ep.entity}")
        if (ep.method, ep.path) in seen: raise SpecError(f"duplicate endpoint {ep.method} {ep.path}")
        seen.add((ep.method, ep.path)); fn = {f.name for f in e.fields}
        want = {"create": "POST", "get": "GET", "list": "GET", "update": "PATCH"}[ep.operation]
        if ep.method != want: raise SpecError(f"endpoint {ep.path}: {ep.operation} must use {want}")
        pk = spec.pk(e).name
        if ep.operation in ("get", "update") and "{" + pk + "}" not in ep.path and "{id}" not in ep.path: raise SpecError(f"endpoint {ep.path}: path must contain {{{pk}}} (or {{id}})")
        if ep.operation in ("create", "list") and "{" in ep.path: raise SpecError(f"endpoint {ep.path}: {ep.operation} paths take no path parameter")
        for n in ep.filters + ep.updatable + ([ep.verify_member] if ep.verify_member else []):
            if n not in fn: raise SpecError(f"endpoint {ep.path}: unknown field {n}")
        for n in ep.updatable:
            f = spec.field(e, n)
            if f.primary_key: raise SpecError(f"endpoint {ep.path}: the primary key cannot be updated")
        if ep.operation == "update" and not ep.updatable: raise SpecError(f"endpoint {ep.path}: update needs updatable fields")
        if ep.transitions:
            en = [spec.field(e, n) for n in ep.updatable if spec.field(e, n).type == "enum"]
            if not en: raise SpecError(f"endpoint {ep.path}: transitions need an updatable enum field")
            vals = set(en[0].values if isinstance(en[0].values, list) else en[0].values.keys())
            for a, bs in ep.transitions.items():
                if a not in vals or any(b not in vals for b in bs): raise SpecError(f"endpoint {ep.path}: transitions use values outside {en[0].name}")
        if ep.verify_member:
            vf = spec.field(e, ep.verify_member)
            if vf.type != "ref" or vf.ref != "member_demographics.member_id": raise SpecError(f"endpoint {ep.path}: verify_member must name a ref to member_demographics.member_id")
        if ("/v1" + ep.path) in set(existing_paths) or ep.path in set(existing_paths): raise SpecError(f"endpoint {ep.method} {ep.path} collides with a built-in route")
    return spec
