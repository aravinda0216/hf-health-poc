"""Run store: one directory per run holding run.json (state) plus the stage artifacts. Writes are atomic (temp file + rename) so a crash never leaves a half-written record."""
import json, os, tempfile, threading, time
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class Store:
    def __init__(self, root): self.root = root; os.makedirs(root, exist_ok=True); self._locks = {}; self._g = threading.Lock()
    def dir(self, run_id): return os.path.join(self.root, run_id)
    def lock(self, run_id):
        with self._g: return self._locks.setdefault(run_id, threading.Lock())
    def exists(self, run_id): return os.path.exists(os.path.join(self.dir(run_id), "run.json"))
    def load(self, run_id):
        with open(os.path.join(self.dir(run_id), "run.json")) as f: return json.load(f)
    def save(self, run):
        d = self.dir(run["run_id"]); os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w") as f: json.dump(run, f, indent=1)
        os.replace(tmp, os.path.join(d, "run.json"))
    def list(self):
        out = []
        for n in sorted(os.listdir(self.root)):
            if self.exists(n): r = self.load(n); out.append({k: r[k] for k in ("run_id", "dataset_id", "state", "created_at", "updated_at")})
        return out
