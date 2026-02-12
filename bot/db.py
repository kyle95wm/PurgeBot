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
"""


async def ensure_db() -> None:
    # Ensure directory exists
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)

    async with aiosqlite.connect(SQLITE_PATH) as db:
        await db.executescript(CREATE_SQL)
        await db.commit()


async def connect():
    db = await aiosqlite.connect(SQLITE_PATH)
    db.row_factory = aiosqlite.Row
    return db
