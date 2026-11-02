"""BM25-style lexical backend (the original in-memory search, moved out of api/app.py unchanged in behaviour)."""
import math, os, re
from .base import Result

STOP = set("the a an and or of to in is it for on with how do i what does my this that are be can we you your will which who when where why long much many from by if as was were been has have there their".split())


def stem(w):
    # Light stemming (plural / -ing / -ed) so "appeals" matches "appeal".
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4: return w[:-len(suf)]
    return w


def tokens(s): return [stem(w) for w in re.findall(r"[a-z0-9\-]+", s.lower()) if w not in STOP]


class LexicalBackend:
    name = "lexical"

    def __init__(self, score_min=None, coverage_min=None):
        self.score_min = float(os.environ.get("HF_KB_SCORE_MIN", "2.0")) if score_min is None else score_min          # minimum BM25-style score to answer
        self.coverage_min = float(os.environ.get("HF_KB_COVERAGE_MIN", "0.4")) if coverage_min is None else coverage_min  # minimum idf-weighted query coverage
        self.docs = []; self.df = {}; self.N = 0; self.avg = 1.0

    def build(self, chunks):
        self.docs = [(c, tokens(c.section_path + " " + c.text)) for c in chunks]; self.df = {}
        for _, t in self.docs:
            for w in set(t): self.df[w] = self.df.get(w, 0) + 1
        self.N = len(self.docs); self.avg = sum(len(t) for _, t in self.docs) / max(1, self.N)

    def _idf(self, w): d = self.df.get(w, 0); return math.log(1 + (self.N - d + .5) / (d + .5))

    def query(self, q, audience, use_case, k):
        qt = tokens(q); uq = set(qt); scored = []
        for c, t in self.docs:
            if c.audience != audience: continue
            if use_case and use_case not in c.use_cases: continue
            tf = {}
            for w in t: tf[w] = tf.get(w, 0) + 1
            s = 0.0
            for w in qt:
                if w in tf: f = tf[w]; s += self._idf(w) * (f * 2.2) / (f + 1.2 * (.25 + .75 * len(t) / self.avg))
            hit = sum(1 for w in uq if w in tf)
            # idf-weighted coverage: the share of the query's information that this chunk contains; off-topic words pull it down.
            cov = sum(self._idf(w) for w in uq if w in tf) / max(1e-9, sum(self._idf(w) for w in uq))
            if s > 0: scored.append((s * (hit / max(1, len(uq))) ** 0.5, c.chunk_id, cov))
        scored.sort(key=lambda x: -x[0]); top = scored[:k]
        abstain = (not top) or top[0][0] < self.score_min or top[0][2] < self.coverage_min
        return Result([] if abstain else [(cid, round(s, 3)) for s, cid, _ in top], abstain)
