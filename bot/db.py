import os
import aiosqlite

from .config import SQLITE_PATH

CREATE_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS invite_baseline (
  guild_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  uses INTEGER NOT NULL,
  inviter_id INTEGER,
  created_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (guild_id, code)
);

CREATE TABLE IF NOT EXISTS invite_join_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  member_id INTEGER NOT NULL,
  member_tag TEXT,
  joined_at TEXT NOT NULL,
  invite_code TEXT,
  inviter_id INTEGER,
  uses_before INTEGER,
  uses_after INTEGER
);

CREATE INDEX IF NOT EXISTS idx_invite_join_log_guild_time
  ON invite_join_log (guild_id, joined_at);

-- Staff-managed server availability for move_server
-- is_open: 1=open, 0=closed
-- until_ts: optional unix seconds; if set and in the past, treated as open and row is auto-cleared
CREATE TABLE IF NOT EXISTS server_status (
  guild_id INTEGER NOT NULL,
  role_id INTEGER NOT NULL,
  is_open INTEGER NOT NULL,
  note TEXT,
  until_ts INTEGER,
  updated_by INTEGER,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (guild_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_server_status_guild
  ON server_status (guild_id);
"""


def connect():
    """
    Return an aiosqlite connection context manager.
    Usage: async with connect() as db:
    """
    return aiosqlite.connect(SQLITE_PATH)


async def ensure_db() -> None:
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

    async with connect() as db:
        await db.executescript(CREATE_SQL)
        await db.commit()
