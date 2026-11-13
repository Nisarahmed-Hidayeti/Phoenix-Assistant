"""Explicit preferences and content-free activity metadata."""
import os
import sqlite3
import stat
from pathlib import Path


class Storage:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(path.parent, flags)
        try:
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError("Phoenix needs an owner-only state directory (mode 0700).")
            fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise OSError("The Phoenix state database must be a private regular file.")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            self.db = sqlite3.connect(path)
        finally:
            os.close(parent)
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA trusted_schema=OFF")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity (
                stamp TEXT DEFAULT CURRENT_TIMESTAMP, action TEXT NOT NULL, outcome TEXT NOT NULL
            );
        """)

    def remember(self, text, key=None):
        with self.db:
            if key is None:
                self.db.execute("INSERT INTO memory(text) VALUES (?)", (text,))
            else:
                self.db.execute("UPDATE memory SET text=? WHERE id=?", (text, key))

    def memories(self):
        return self.db.execute("SELECT id,text FROM memory ORDER BY id DESC").fetchall()

    def forget(self, key):
        with self.db:
            self.db.execute("DELETE FROM memory WHERE id=?", (key,))

    def log(self, action, outcome):
        with self.db:
            self.db.execute("INSERT INTO activity(action,outcome) VALUES (?,?)", (action, outcome))

    def history(self):
        return self.db.execute("SELECT stamp,action,outcome FROM activity ORDER BY rowid DESC LIMIT 100").fetchall()
