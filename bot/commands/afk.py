import datetime as dt
import re
from typing import Optional

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS
from ..db import connect


AFK_NOTIFY_COOLDOWN_SECONDS = 60  # silent cooldown per (pinger, afk_user)
WELCOME_BACK_DELETE_SECONDS = 5 * 60  # 5 minutes

# (pinger_id, afk_user_id) -> last notified timestamp
_LAST_AFK_NOTIFY: dict[tuple[int, int], dt.datetime] = {}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_now() -> str:
    return _now().isoformat()


def _rel_ts(d: dt.datetime | None) -> str:
    if not d:
        return "unknown"
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return f"<t:{int(d.timestamp())}:R>"


def _abs_ts(d: dt.datetime | None) -> str:
    if not d:
        return "unknown"
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return f"<t:{int(d.timestamp())}:F>"


async def _ensure_table() -> None:
    async with connect() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS afk_status (
              guild_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              message TEXT,
              until_ts INTEGER,
              set_at TEXT NOT NULL,
              PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        await db.commit()


def _parse_until(s: str) -> Optional[int]:
    """
    Accepts:
      - Discord timestamps: <t:1700000000:R>, <t:1700000000:F>, etc
      - Relative like: 2h, 30m, 1d, 45s, 2w
    Returns unix seconds or None.
    """
    s = s.strip()

    # Discord-style: <t:UNIX:...>
    m = re.match(r"^<t:(\d+)(?::[tTdDfFR])?>$", s)
    if m:
        return int(m.group(1))

    # Relative: number + unit
    m = re.match(r"^(\d+)\s*([smhdw])$", s, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return int((_now().timestamp()) + n * mult)

    return None


async def _set_afk(*, guild_id: int, user_id: int, message: Optional[str], until_ts: Optional[int]) -> None:
    await _ensure_table()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO afk_status (guild_id, user_id, message, until_ts, set_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
              message=excluded.message,
              until_ts=excluded.until_ts,
              set_at=excluded.set_at
            """,
            (guild_id, user_id, message, until_ts, _iso_now()),
        )
        await db.commit()


async def _clear_afk(*, guild_id: int, user_id: int) -> bool:
    await _ensure_table()
    async with connect() as db:
        cur = await db.execute(
            "DELETE FROM afk_status WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def _get_afk(*, guild_id: int, user_id: int) -> Optional[dict]:
    await _ensure_table()
    async with connect() as db:
        cur = await db.execute(
            "SELECT message, until_ts, set_at FROM afk_status WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        message, until_ts, set_at = row
        set_at_dt = None
        try:
            set_at_dt = dt.datetime.fromisoformat(set_at)
            if set_at_dt.tzinfo is None:
                set_at_dt = set_at_dt.replace(tzinfo=dt.timezone.utc)
        except Exception:
            set_at_dt = None
        return {"message": message, "until_ts": until_ts, "set_at": set_at_dt}


async def _is_afk(*, guild_id: int, user_id: int) -> bool:
    return (await _get_afk(guild_id=guild_id, user_id=user_id)) is not None


def _can_notify(pinger_id: int, afk_user_id: int) -> bool:
    now = _now()
    key = (pinger_id, afk_user_id)
    last = _LAST_AFK_NOTIFY.get(key)
    if last and (now - last).total_seconds() < AFK_NOTIFY_COOLDOWN_SECONDS:
        return False
    _LAST_AFK_NOTIFY[key] = now
    return True


def _red_embed(title: str, desc: str) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=0xED4245)  # red-ish


def _green_embed(title: str, desc: str) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=0x57F287)  # green-ish


def _build_afk_status_embed(*, member: discord.abc.User, afk_data: Optional[dict]) -> discord.Embed:
    """
    Green embed if not AFK, red if AFK. Used by /afk_status.
    """
    if not afk_data:
        return _green_embed("AFK status", f"{member.mention} is not AFK.")

    lines: list[str] = [f"{member.mention} is AFK."]

    msg_txt = (afk_data.get("message") or "").strip()
    if msg_txt:
        lines.append(f"Message: {msg_txt}")

    until_ts = afk_data.get("until_ts")
    if until_ts:
        try:
            eta_dt = dt.datetime.fromtimestamp(int(until_ts), tz=dt.timezone.utc)
            lines.append(f"ETA: {_abs_ts(eta_dt)} ({_rel_ts(eta_dt)})")
        except Exception:
            pass

    set_at = afk_data.get("set_at")
    if set_at:
        lines.append(f"Set: {_rel_ts(set_at)}")

    return _red_embed("AFK status", "\n".join(lines))


async def _notify_afk(message: discord.Message, afk_member: discord.Member, afk_data: dict) -> None:
    guild = message.guild
    if guild is None:
        return

    # silent cooldown
    if not _can_notify(message.author.id, afk_member.id):
        return

    reason_lines: list[str] = []
    reason_lines.append(f"**{afk_member.display_name}** is AFK.")

    msg_txt = (afk_data.get("message") or "").strip()
    if msg_txt:
        reason_lines.append(f"Message: {msg_txt}")

    until_ts = afk_data.get("until_ts")
    if until_ts:
        try:
            eta_dt = dt.datetime.fromtimestamp(int(until_ts), tz=dt.timezone.utc)
            reason_lines.append(f"ETA: {_abs_ts(eta_dt)} ({_rel_ts(eta_dt)})")
        except Exception:
            pass

    set_at = afk_data.get("set_at")
    if set_at:
        reason_lines.append(f"Set: {_rel_ts(set_at)}")

    embed = _red_embed("AFK", "\n".join(reason_lines))

    try:
        await message.reply(embed=embed, allowed_mentions=NO_PINGS, mention_author=False)
    except Exception:
        # if reply fails (perms), try a plain send
        try:
            await message.channel.send(embed=embed, allowed_mentions=NO_PINGS)
        except Exception:
            return


async def _handle_return(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        return
    if message.author.bot:
        return
    if not isinstance(message.author, discord.Member):
        return

    afk_data = await _get_afk(guild_id=guild.id, user_id=message.author.id)
    if not afk_data:
        return

    # clear AFK
    await _clear_afk(guild_id=guild.id, user_id=message.author.id)

    embed = _green_embed(
        "Welcome back",
        f"{message.author.mention} is no longer AFK.",
    )

    try:
        sent = await message.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
        await sent.delete(delay=WELCOME_BACK_DELETE_SECONDS)
    except Exception:
        return


async def _handle_mentions_and_replies(message: discord.Message) -> None:
    guild = message.guild
    if guild is None:
        return
    if message.author.bot:
        return

    # 1) Direct mentions
    mentioned = []
    try:
        mentioned = [m for m in message.mentions if isinstance(m, discord.Member) and not m.bot]
    except Exception:
        mentioned = []

    # 2) Reply target author
    replied_member: Optional[discord.Member] = None
    if message.reference and message.reference.resolved:
        try:
            ref_msg = message.reference.resolved
            if isinstance(ref_msg, discord.Message) and isinstance(ref_msg.author, discord.Member):
                replied_member = ref_msg.author
        except Exception:
            replied_member = None

    # Deduplicate
    targets: dict[int, discord.Member] = {}
    for m in mentioned:
        targets[m.id] = m
    if replied_member:
        targets[replied_member.id] = replied_member

    if not targets:
        return

    # Notify for each AFK target
    for uid, member in targets.items():
        afk_data = await _get_afk(guild_id=guild.id, user_id=uid)
        if not afk_data:
            continue
        await _notify_afk(message, member, afk_data)


def setup(bot):
    # -------------------------
    # /afk command (anyone)
    # -------------------------
    @bot.tree.command(name="afk", description="Set yourself as AFK (optional ETA + message).")
    @app_commands.describe(
        when="Optional ETA (Discord timestamp like <t:...:R> or relative like 2h, 30m, 1d).",
        note="Optional note to show when someone pings/replies to you.",
    )
    async def afk(interaction: discord.Interaction, when: str | None = None, note: str | None = None):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        until_ts: Optional[int] = None
        if when:
            until_ts = _parse_until(when)
            if until_ts is None:
                await interaction.response.send_message(
                    "Couldn’t parse that time. Use `<t:UNIX:R>` or a relative like `2h`, `30m`, `1d`.",
                    ephemeral=True,
                )
                return

        await _set_afk(
            guild_id=guild.id,
            user_id=interaction.user.id,
            message=(note.strip() if note else None),
            until_ts=until_ts,
        )

        desc_lines = ["You’re now marked as **AFK**."]
        if note:
            desc_lines.append(f"Message: {note.strip()}")
        if until_ts:
            eta_dt = dt.datetime.fromtimestamp(int(until_ts), tz=dt.timezone.utc)
            desc_lines.append(f"ETA: {_abs_ts(eta_dt)} ({_rel_ts(eta_dt)})")

        embed = _red_embed("AFK enabled", "\n".join(desc_lines))
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    # -------------------------
    # /afk_status (anyone)
    # -------------------------
    @bot.tree.command(name="afk_status", description="Check AFK status for yourself or another user.")
    @app_commands.describe(user="User to check (defaults to you).")
    async def afk_status(interaction: discord.Interaction, user: discord.Member | None = None):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        target = user or interaction.user
        if isinstance(target, discord.User) and not isinstance(target, discord.Member):
            # should be rare for this command signature, but keep it safe
            await interaction.response.send_message("Could not resolve that member in this server.", ephemeral=True)
            return

        afk_data = await _get_afk(guild_id=guild.id, user_id=target.id)  # type: ignore[arg-type]
        embed = _build_afk_status_embed(member=target, afk_data=afk_data)  # type: ignore[arg-type]
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    # -------------------------
    # /afk_clear (staff-only)
    # -------------------------
    @bot.tree.command(name="afk_clear", description="Staff-only: remove AFK status from a user.")
    @app_commands.describe(user="User to clear AFK for.")
    async def afk_clear(interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        cleared = await _clear_afk(guild_id=guild.id, user_id=user.id)
        if not cleared:
            await interaction.response.send_message("That user is not currently AFK.", ephemeral=True)
            return

        embed = _green_embed("AFK cleared", f"Removed AFK status for {user.mention}.")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

    # -------------------------
    # Message listeners
    # -------------------------
    @bot.listen("on_message")
    async def _afk_on_message(message: discord.Message):
        # Clear AFK on return
        await _handle_return(message)
        # Notify if pinging/replying to AFK users
        await _handle_mentions_and_replies(message)
