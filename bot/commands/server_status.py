import datetime as dt
import re
from typing import Optional

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS
from ..db import connect

from .server_roles import SERVER_ROLES


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_now() -> str:
    return _now().isoformat()


def _parse_until(s: str) -> Optional[int]:
    """
    Accepts:
      - Discord timestamps: <t:1700000000:R>, <t:1700000000:F>, etc
      - Relative like: 2h, 30m, 1d, 45s, 2w
      - Raw unix seconds: 1700000000
    Returns unix seconds or None.
    """
    s = s.strip()

    if re.fullmatch(r"\d{9,12}", s):
        try:
            return int(s)
        except Exception:
            return None

    m = re.match(r"^<t:(\d+)(?::[tTdDfFR])?>$", s)
    if m:
        return int(m.group(1))

    m = re.match(r"^(\d+)\s*([smhdw])$", s, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return int((_now().timestamp()) + n * mult)

    return None


async def _set_status(
    *,
    guild_id: int,
    role_id: int,
    is_open: bool,
    note: Optional[str],
    until_ts: Optional[int],
    updated_by: int,
) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO server_status (guild_id, role_id, is_open, note, until_ts, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, role_id) DO UPDATE SET
              is_open=excluded.is_open,
              note=excluded.note,
              until_ts=excluded.until_ts,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (
                guild_id,
                role_id,
                1 if is_open else 0,
                (note.strip() if note else None),
                until_ts,
                updated_by,
                _iso_now(),
            ),
        )
        await db.commit()


async def _clear_status(*, guild_id: int, role_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM server_status WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def _get_status_row(*, guild_id: int, role_id: int) -> Optional[dict]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT is_open, note, until_ts, updated_by, updated_at FROM server_status WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        row = await cur.fetchone()
        if not row:
            return None

        is_open, note, until_ts, updated_by, updated_at = row
        return {
            "is_open": bool(is_open),
            "note": note,
            "until_ts": until_ts,
            "updated_by": updated_by,
            "updated_at": updated_at,
        }


async def get_effective_status(*, guild_id: int, role_id: int) -> dict:
    row = await _get_status_row(guild_id=guild_id, role_id=role_id)
    if not row:
        return {"is_open": True, "note": None, "until_ts": None, "source": "default"}

    until_ts = row.get("until_ts")
    if until_ts:
        try:
            if int(until_ts) <= int(_now().timestamp()):
                await _clear_status(guild_id=guild_id, role_id=role_id)
                return {"is_open": True, "note": None, "until_ts": None, "source": "default"}
        except Exception:
            pass

    return {
        "is_open": bool(row.get("is_open")),
        "note": row.get("note"),
        "until_ts": row.get("until_ts"),
        "source": "override",
    }


async def list_statuses(*, guild_id: int) -> list[tuple[int, dict]]:
    return [(rid, await get_effective_status(guild_id=guild_id, role_id=rid)) for rid in SERVER_ROLES.keys()]


def _abs_ts(unix_s: int) -> str:
    try:
        d = dt.datetime.fromtimestamp(int(unix_s), tz=dt.timezone.utc)
        return f"<t:{int(d.timestamp())}:F>"
    except Exception:
        return str(unix_s)


def _rel_ts(unix_s: int) -> str:
    try:
        d = dt.datetime.fromtimestamp(int(unix_s), tz=dt.timezone.utc)
        return f"<t:{int(d.timestamp())}:R>"
    except Exception:
        return ""


def _server_choices() -> list[app_commands.Choice[int]]:
    return [app_commands.Choice(name=name[:100], value=int(rid)) for rid, name in SERVER_ROLES.items()]


class ServerStatusGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="server_status", description="Staff-only: manage move destination availability.")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this.", ephemeral=True)
            return False
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="list", description="List open/closed status for all servers.")
    async def list_cmd(self, interaction: discord.Interaction):
        guild = interaction.guild
        assert guild is not None

        rows = await list_statuses(guild_id=guild.id)

        embed = discord.Embed(title="Server status")
        lines = []
        for role_id, st in rows:
            name = SERVER_ROLES.get(role_id, str(role_id))
            state = "✅ Open" if st["is_open"] else "⛔ Closed"
            extra = []
            if st.get("note"):
                extra.append(st["note"])
            if st.get("until_ts"):
                extra.append(f"until {_abs_ts(int(st['until_ts']))} ({_rel_ts(int(st['until_ts']))})")
            tail = f" — {' | '.join(extra)}" if extra else ""
            src = "" if st.get("source") == "override" else " (default)"
            lines.append(f"• **{name}**: {state}{src}{tail}")

        embed.description = "\n".join(lines) if lines else "(none)"
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    @app_commands.command(name="set", description="Set a server to open/closed with an optional note and optional expiry.")
    @app_commands.describe(
        server="Which server (by its role mapping).",
        open="True=open, False=closed.",
        note="Optional note staff can see.",
        until="Optional expiry (raw unix, <t:...:R>, or relative like 2h/1d).",
    )
    @app_commands.choices(server=_server_choices())
    async def set_cmd(
        self,
        interaction: discord.Interaction,
        server: app_commands.Choice[int],
        open: bool,
        note: str | None = None,
        until: str | None = None,
    ):
        guild = interaction.guild
        assert guild is not None

        until_ts: Optional[int] = None
        if until:
            until_ts = _parse_until(until)
            if until_ts is None:
                await interaction.response.send_message(
                    "Couldn’t parse `until`. Use raw unix seconds, `<t:UNIX:R>`, or a relative like `2h`, `30m`, `1d`.",
                    ephemeral=True,
                )
                return

        await _set_status(
            guild_id=guild.id,
            role_id=int(server.value),
            is_open=bool(open),
            note=note,
            until_ts=until_ts,
            updated_by=interaction.user.id,
        )

        name = SERVER_ROLES.get(int(server.value), str(server.value))
        state = "OPEN" if open else "CLOSED"
        extra = []
        if note:
            extra.append(f"Note: {note.strip()}")
        if until_ts:
            extra.append(f"Until: {_abs_ts(until_ts)} ({_rel_ts(until_ts)})")

        msg = f"Set **{name}** to **{state}**." + (f"\n" + "\n".join(extra) if extra else "")
        await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=NO_PINGS)

    @app_commands.command(name="clear", description="Clear override for a server (reverts to default open).")
    @app_commands.describe(server="Which server (by its role mapping).")
    @app_commands.choices(server=_server_choices())
    async def clear_cmd(self, interaction: discord.Interaction, server: app_commands.Choice[int]):
        guild = interaction.guild
        assert guild is not None

        ok = await _clear_status(guild_id=guild.id, role_id=int(server.value))
        name = SERVER_ROLES.get(int(server.value), str(server.value))
        if ok:
            await interaction.response.send_message(f"Cleared override for **{name}** (default: open).", ephemeral=True)
        else:
            await interaction.response.send_message(f"No override existed for **{name}**.", ephemeral=True)


def setup(bot):
    bot.tree.add_command(ServerStatusGroup())
