"""Local and in-memory profiles, both SQLite.

local   : dataset opened read-only from a file; state is a file next to it.
memory  : dataset is copied once into a shared in-memory database at startup (fast, no file needed afterwards); state is a separate in-memory database that vanishes
          when the process exits. Nothing is written to disk.
"""
import sqlite3, threading

_mem = {}; _mem_lock = threading.Lock()


def _shared(name):
    """Open another connection to the named shared in-memory database."""
    c = sqlite3.connect(f"file:{name}?mode=memory&cache=shared", uri=True, check_same_thread=False, timeout=30); c.row_factory = sqlite3.Row; return c


class SqliteDataset:
    def __init__(self, path): self.path = path
    def connect_ro(self):
        c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False); c.row_factory = sqlite3.Row; return c


class SqliteState:
    def __init__(self, path): self.path = path
    def connect(self):
        c = sqlite3.connect(self.path, check_same_thread=False); c.row_factory = sqlite3.Row; return c


class MemoryDataset:
    """Loads the dataset file into a shared in-memory database once. A held connection keeps the database alive; readers get their own connections."""
    def __init__(self, path, name="hf_dataset"):
        self.name = f"{name}_{abs(hash(path))}"
        with _mem_lock:
            if self.name not in _mem:
                keep = _shared(self.name); src = sqlite3.connect(f"file:{path}?mode=ro", uri=True); src.backup(keep); src.close(); _mem[self.name] = keep
    def connect_ro(self):
        c = _shared(self.name); c.execute("PRAGMA query_only=ON"); return c


class MemoryState:
    def __init__(self, name="hf_state"):
        self.name = f"{name}_{id(self)}"; self._keep = _shared(self.name)
    def connect(self): return _shared(self.name)
