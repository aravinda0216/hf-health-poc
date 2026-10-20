"""Deterministic primitives: seeded RNG, UUIDv5 ids, simulated clock, canonical hashing.

Rules enforced here (architecture doc, section 6):
  * every row's randomness comes from H(root_seed, stage, entity, row_index) -- never shard/worker index
  * ids are UUIDv5(dataset_id, entity, seed, row_index[, sub])
  * generators never read the wall clock; all dates derive from as_of_date
"""
import hashlib, json, random, uuid
from datetime import date, datetime, timedelta

# Version stamps. They feed data_spec_hash, so bumping any of them intentionally changes the dataset fingerprint.
GEN_VERSION = "0.1.0"
CATALOG_VERSION = "2.4.0"
RULESET_VERSION = "1.8.0"
# Row hashes are summed modulo 2**64, which makes the per-entity checksum independent of row order.
MASK64 = (1 << 64) - 1


# Canonical JSON (sorted keys, no spaces): the same object always serializes to the same bytes, so hashes are stable.
def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def sha(o) -> str:
    return "sha256:" + sha256_hex(canon(o) if not isinstance(o, str) else o)


# 64-bit hash of the joined parts. This is the only source of randomness: seeds are derived from it, never from time or process state.
def h64(*parts) -> int:
    return int.from_bytes(hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest(), "big")


# Hash of one row's values (None/dates become strings). Summed per entity to build the order-independent checksum in the manifest.
def row_hash(values) -> int:
    return int.from_bytes(hashlib.blake2b(json.dumps(values, separators=(",", ":"), default=str).encode(), digest_size=8).digest(), "big")


# Thin helper over random.Random. All draws go through u()/i()/pick()/chance()/weighted() so call order stays reproducible.
class Rng(random.Random):
    def u(self): return self.random()
    def i(self, a, b): return a + int(self.random() * (b - a + 1))
    def pick(self, seq): return seq[int(self.random() * len(seq))]
    def chance(self, p): return self.random() < p
    def weighted(self, pairs):
        t = self.random() * sum(w for _, w in pairs)
        for k, w in pairs:
            t -= w
            if t < 0: return k
        return pairs[-1][0]


class Ctx:
    """Run context shared by all generators."""
    def __init__(self, dataset_id, seed, as_of, edge_pct=5.0, vol=None):
        self.dataset_id, self.seed, self.edge = dataset_id, int(seed), float(edge_pct)
        self.as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        self.now = datetime(self.as_of.year, self.as_of.month, self.as_of.day, 12, 0, 0)
        self.vol = vol
        self._ns = uuid.uuid5(uuid.NAMESPACE_URL, "hfactory:" + dataset_id)

    # Stable id: same (dataset, entity, seed, index, sub) always gives the same UUID, on any machine.
    def id(self, entity, idx, sub=""):
        return str(uuid.uuid5(self._ns, f"{entity}|{self.seed}|{idx}|{sub}"))

    # Per-row generator. Seeding by (seed, stage, entity, row index) means a row never depends on how many rows came before it,
    # which is what lets 'evolve' (more members) keep the earlier rows unchanged.
    def rng(self, stage, entity, idx):
        return Rng(h64(self.seed, stage, entity, idx))

    # Dates are offsets from as_of_date; the wall clock is never read.
    def d(self, days=0):
        return (self.as_of + timedelta(days=days)).isoformat()

    def ts(self, days=0, hours=0, base=None):
        return ((base or self.now) + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Luhn check digit as used for NPIs (the real algorithm prefixes the 80840 issuer code).
def luhn_check_digit(payload: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(payload)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9: n -= 9
        total += n
    return (10 - total % 10) % 10


def npi_valid(npi: str) -> bool:
    return isinstance(npi, str) and len(npi) == 10 and npi.isdigit() and luhn_check_digit("80840" + npi[:9]) == int(npi[9])


def make_npi(k: int) -> str:
    """Luhn-valid; leading '9' is outside the real NPI range (real NPIs start 1 or 2) so it cannot collide with a real provider."""
    body = "9" + f"{(k * 7919 + 1234567) % 10**8:08d}"
    return body + str(luhn_check_digit("80840" + body))


# Synthetic TIN. The '99-' prefix is not a valid IRS prefix, so a generated TIN cannot belong to a real organization.
def make_tin(group: int) -> str:
    return f"99-{(group * 104729 + 7654321) % 10**7:07d}"


# Money is held as integer cents everywhere in the generators to avoid float drift; money() converts to dollars at the edge.
def cents(x): return int(round(x))
def money(c): return None if c is None else round(c / 100.0, 2)
