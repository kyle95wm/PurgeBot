import datetime as dt
from typing import Optional

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS
from ..db import connect

from .server_roles import SERVER_ROLES


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with connect() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS server_status (
              guild_id INTEGER NOT NULL,
              role_id INTEGER NOT NULL,
              is_open INTEGER NOT NULL,
              note TEXT,
              updated_at TEXT NOT NULL,
              updated_by INTEGER,
              PRIMARY KEY (guild_id, role_id)
            )
            """
        )
        await db.commit()


async def set_status(*, guild_id: int, role_id: int, is_open: bool, note: Optional[str], updated_by: int) -> None:
    await _ensure_table()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO server_status (guild_id, role_id, is_open, note, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, role_id) DO UPDATE SET
              is_open=excluded.is_open,
              note=excluded.note,
              updated_at=excluded.updated_at,
              updated_by=excluded.updated_by
            """,
            (guild_id, role_id, 1 if is_open else 0, note, _iso_now(), updated_by),
        )
        await db.commit()


async def clear_status(*, guild_id: int, role_id: int) -> bool:
    await _ensure_table()
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM server_status WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def get_effective_status(*, guild_id: int, role_id: int) -> dict:
    """
    Default is OPEN unless overridden in DB.
    Returns:
      {"is_open": bool, "note": str|None, "updated_at": str|None, "updated_by": int|None, "is_default": bool}
    """
    await _ensure_table()
    async with connect() as db:
        cur = await db.execute(
            "SELECT is_open, note, updated_at, updated_by FROM server_status WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        row = await cur.fetchone()

    if not row:
        return {"is_open": True, "note": None, "updated_at": None, "updated_by": None, "is_default": True}

    is_open_i, note, updated_at, updated_by = row
    return {
        "is_open": bool(is_open_i),
        "note": note,
        "updated_at": updated_at,
        "updated_by": updated_by,
        "is_default": False,
    }


def _server_choices() -> list[app_commands.Choice[str]]:
    # choices values must be <= JS safe integer if numeric, so we use strings
    return [app_commands.Choice(name=name, value=str(rid)) for rid, name in SERVER_ROLES.items()]


def _status_embed(title: str, desc: str, *, ok: bool) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=(0x57F287 if ok else 0xED4245))


class ServerStatusGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="server_status", description="Staff: manage server availability for move requests.")

    @app_commands.command(name="set", description="Set a server as open/closed (with optional note).")
    @app_commands.describe(server="Which server role this applies to.", open="Whether the server is open.", note="Optional note shown to staff/users.")
    @app_commands.choices(server=_server_choices())
    async def set_cmd(self, interaction: discord.Interaction, server: str, open: bool, note: str | None = None):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        role_id = int(server)
        await set_status(
            guild_id=interaction.guild.id,
            role_id=role_id,
            is_open=open,
            note=(note.strip() if note else None),
            updated_by=interaction.user.id,
        )

        name = SERVER_ROLES.get(role_id, str(role_id))
        state = "OPEN ✅" if open else "CLOSED ⛔"
        extra = f"\nNote: {note.strip()}" if note and note.strip() else ""
        embed = _status_embed("Server status updated", f"**{name}** is now **{state}**.{extra}", ok=open)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    @app_commands.command(name="clear", description="Clear override (reverts to default: open).")
    @app_commands.describe(server="Which server role to clear override for.")
    @app_commands.choices(server=_server_choices())
    async def clear_cmd(self, interaction: discord.Interaction, server: str):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        role_id = int(server)
        cleared = await clear_status(guild_id=interaction.guild.id, role_id=role_id)

        name = SERVER_ROLES.get(role_id, str(role_id))
        if not cleared:
            await interaction.response.send_message(f"No override existed for **{name}**.", ephemeral=True)
            return

        embed = _status_embed("Override cleared", f"**{name}** is back to default (**OPEN**).", ok=True)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    @app_commands.command(name="list", description="Show current server status (default is open).")
    async def list_cmd(self, interaction: discord.Interaction):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        lines: list[str] = []
        for rid, name in SERVER_ROLES.items():
            st = await get_effective_status(guild_id=interaction.guild.id, role_id=rid)
            if st["is_default"]:
                lines.append(f"**{name}** — OPEN ✅ (default)")
            else:
                state = "OPEN ✅" if st["is_open"] else "CLOSED ⛔"
                note = f" — {st['note']}" if st.get("note") else ""
                lines.append(f"**{name}** — {state}{note}")

        embed = discord.Embed(title="Server Status", description="\n".join(lines) if lines else "No servers configured.", color=0xA9C9FF)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)


def setup(bot):
    bot.tree.add_command(ServerStatusGroup())
