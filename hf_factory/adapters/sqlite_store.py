"""Local profile: SQLite files. This is the only fully working binding."""
import sqlite3


class SqliteDataset:
    def __init__(self, path): self.path = path
    def connect_ro(self):
        c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False); c.row_factory = sqlite3.Row; return c


class SqliteState:
    def __init__(self, path): self.path = path
    def connect(self):
        c = sqlite3.connect(self.path, check_same_thread=False); c.row_factory = sqlite3.Row; return c
