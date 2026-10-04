"""Game storage in SQLite, so several gunicorn workers share the same games.

Each row has a version number. A save only succeeds if nobody saved in between
(optimistic locking), so two requests racing on one game can't both apply a move.
"""
import json
import secrets
import sqlite3
import time

from engine import Game


class StoreFull(Exception):
    pass


class GameStore:
    def __init__(self, path, ttl_seconds=24 * 3600, max_games=5000):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.max_games = max_games
        self._init_schema()

    def _init_schema(self):
        """Create the table and switch to WAL mode. Several gunicorn workers start at the same
        moment and all run this; switching journal mode needs an exclusive lock and doesn't wait
        for a busy database, so a worker that loses the race backs off and tries again."""
        for attempt in range(30):
            try:
                with self._connect() as conn:
                    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                        conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("""CREATE TABLE IF NOT EXISTS games (
                                        id TEXT PRIMARY KEY,
                                        state TEXT NOT NULL,
                                        version INTEGER NOT NULL,
                                        updated REAL NOT NULL)""")
                    conn.execute("CREATE INDEX IF NOT EXISTS games_updated ON games(updated)")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or attempt == 29:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _dump(game, seats):
        # seats: {"white": sha256 hex or None, "black": ...} for online games, else None
        return json.dumps({"game": game.to_dict(), "seats": seats})

    def create(self, game, seats=None):
        now = time.time()
        game_id = secrets.token_urlsafe(16)
        with self._connect() as conn:
            conn.execute("DELETE FROM games WHERE updated < ?", (now - self.ttl_seconds,))
            (count,) = conn.execute("SELECT COUNT(*) FROM games").fetchone()
            if count >= self.max_games:
                raise StoreFull
            conn.execute("INSERT INTO games (id, state, version, updated) VALUES (?, ?, 0, ?)",
                         (game_id, self._dump(game, seats), now))
        return game_id

    def load(self, game_id):
        """(game, version, seats) or None if missing or expired."""
        with self._connect() as conn:
            row = conn.execute("SELECT state, version, updated FROM games WHERE id = ?", (game_id,)).fetchone()
        if row is None or row[2] < time.time() - self.ttl_seconds:
            return None
        record = json.loads(row[0])
        return Game.from_dict(record["game"]), row[1], record["seats"]

    def version(self, game_id):
        """Current version number only: a cheap check used while long-polling."""
        with self._connect() as conn:
            row = conn.execute("SELECT version FROM games WHERE id = ?", (game_id,)).fetchone()
        return row[0] if row else None

    def save(self, game_id, game, version, seats=None):
        """False if someone else saved this game since it was loaded."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE games SET state = ?, version = version + 1, updated = ? WHERE id = ? AND version = ?",
                (self._dump(game, seats), time.time(), game_id, version))
            return cur.rowcount == 1
