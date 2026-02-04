import datetime as dt
import secrets
from typing import Literal

import discord

from .config import (
    VISITOR_ROLE_ID,
    REDDITOR_ROLE_ID,
    ALLOWED_ROLE_IDS,
    DEFAULT_PURGE_DAYS,
    CHECKME_COOLDOWN_SECONDS,
    CONFIRM_CODE_TTL_SECONDS,
    KICK_DELAY_SECONDS,
    CONFIRM_PHRASE,
    GRACE_PERIOD_SECONDS,
    TICKET_CHANNEL_ID,
    AUDIT_LOG_CHANNEL_ID,
)

NO_PINGS = discord.AllowedMentions.none()

# In-memory state
CHECKME_LAST_USED: dict[int, dt.datetime] = {}
PENDING_PURGES: dict[tuple[int, int], dict] = {}

RoleMode = Literal["both", "redditor_only", "member_only"]


# --------------------
# Misc helpers
# --------------------
def normalize_phrase(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    s = " ".join(s.split())
    return s.upper()


def rel_ts(d: dt.datetime | None) -> str:
    if not d:
        return "unknown"
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return f"<t:{int(d.timestamp())}:R>"


def chunk_lines(lines: list[str], max_chars: int = 900) -> list[str]:
    pages: list[str] = []
    cur = ""
    for line in lines:
        add = line + "\n"
        if len(cur) + len(add) > max_chars:
            pages.append(cur.rstrip() if cur else "(none)")
            cur = add
        else:
            cur += add
    pages.append(cur.rstrip() if cur else "(none)")
    return pages


# --------------------
# Cooldown
# --------------------
def checkme_on_cooldown(user_id: int) -> tuple[bool, int]:
    now = dt.datetime.now(dt.timezone.utc)
    last = CHECKME_LAST_USED.get(user_id)
    if not last:
        return False, 0
    elapsed = (now - last).total_seconds()
    if elapsed >= CHECKME_COOLDOWN_SECONDS:
        return False, 0
    return True, int(CHECKME_COOLDOWN_SECONDS - elapsed)


def mark_checkme_used(user_id: int) -> None:
    CHECKME_LAST_USED[user_id] = dt.datetime.now(dt.timezone.utc)


# --------------------
# Role / time logic
# --------------------
def role_ids_excluding_everyone(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles if r != member.guild.default_role}


def member_matches_role_mode(member: discord.Member, mode: RoleMode) -> bool:
    role_ids = role_ids_excluding_everyone(member)

    # must have Member
    if VISITOR_ROLE_ID not in role_ids:
        return False

    # must not have any other roles besides Member/Redditor
    if not role_ids.issubset(ALLOWED_ROLE_IDS):
        return False

    has_redditor = (REDDITOR_ROLE_ID in role_ids)

    if mode == "both":
        return True
    if mode == "redditor_only":
        return has_redditor
    if mode == "member_only":
        return not has_redditor
    return False


def member_is_time_eligible(member: discord.Member, days: int) -> bool:
    if not member.joined_at:
        return False
    joined = member.joined_at
    if joined.tzinfo is None:
        joined = joined.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    return (now - joined) > dt.timedelta(days=days)


def line_for_member(m: discord.Member) -> str:
    return f"• {m} {m.mention} — {m.id} — joined {rel_ts(m.joined_at)}"


def oldest_first(m: discord.Member):
    j = m.joined_at or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if j.tzinfo is None:
        j = j.replace(tzinfo=dt.timezone.utc)
    return (j, m.id)


def newest_first(m: discord.Member):
    j = m.joined_at or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if j.tzinfo is None:
        j = j.replace(tzinfo=dt.timezone.utc)
    return (j, m.id)


def pretty_role_mode(mode: RoleMode) -> str:
    if mode == "both":
        return "member_only + member+redditor"
    if mode == "redditor_only":
        return "member+redditor only"
    if mode == "member_only":
        return "member only"
    return str(mode)


# --------------------
# /checkme message builder
# --------------------
def build_checkme_message(member: discord.Member) -> str:
    role_ids = role_ids_excluding_everyone(member)
    has_member = VISITOR_ROLE_ID in role_ids
    has_redditor = REDDITOR_ROLE_ID in role_ids
    has_other_roles = not role_ids.issubset(ALLOWED_ROLE_IDS)

    in_scope = has_member and not has_other_roles
    days = DEFAULT_PURGE_DAYS
    time_ok = member_is_time_eligible(member, days)

    joined_str = rel_ts(member.joined_at) if member.joined_at else "unknown"

    lines: list[str] = []
    lines.append("**Purge self-check**")
    lines.append(f"Joined: {joined_str}")
    lines.append("")
    lines.append("**Your roles (as the bot sees them):**")
    lines.append(f"- Has Member: **{has_member}**")
    lines.append(f"- Has Redditor: **{has_redditor}**")
    lines.append(f"- Has other roles: **{has_other_roles}**")
    lines.append("")

    if not in_scope:
        lines.append("✅ **Not at risk** (you’re not in the purge target group).")
    else:
        if time_ok:
            lines.append("⚠️ **At risk** under default purge settings.")
            lines.append("")
            lines.append(f"If this is a mistake or you need access, please open a ticket in <#{TICKET_CHANNEL_ID}>.")
        else:
            lines.append("🟡 **Potentially at risk later** (role-wise you match, but you’re not old enough yet).")
            lines.append(f"You’d become eligible after you’ve been in the server more than **{days}** days.")
            lines.append(f"If you think you should already have another role, open a ticket in <#{TICKET_CHANNEL_ID}>.")

    return "\n".join(lines)


# --------------------
# Purge helpers
# --------------------
async def compute_purge_candidates(
    guild: discord.Guild,
    invoker_id: int,
    bot_id: int,
    days: int,
    include_bots: bool,
    role_mode: RoleMode,
) -> list[discord.Member]:
    candidates: list[discord.Member] = []
    async for m in guild.fetch_members(limit=None):
        if not include_bots and m.bot:
            continue
        if not member_matches_role_mode(m, role_mode):
            continue
        if not member_is_time_eligible(m, days):
            continue
        if m.id in {invoker_id, bot_id}:
            continue
        candidates.append(m)
    candidates.sort(key=oldest_first)
    return candidates


def generate_confirm_code() -> str:
    return secrets.token_hex(3).upper()  # 6 hex chars


async def send_audit_embed(guild: discord.Guild, embed: discord.Embed) -> None:
    if not AUDIT_LOG_CHANNEL_ID:
        return

    ch = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if ch is None:
        try:
            ch = await guild.fetch_channel(AUDIT_LOG_CHANNEL_ID)
        except Exception:
            return

    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        try:
            await ch.send(embed=embed, allowed_mentions=NO_PINGS)
        except Exception:
            return


# Re-export commonly used constants
PURGE_DEFAULT_DAYS = DEFAULT_PURGE_DAYS
PURGE_CONFIRM_TTL_SECONDS = CONFIRM_CODE_TTL_SECONDS
PURGE_KICK_DELAY_SECONDS = KICK_DELAY_SECONDS
PURGE_CONFIRM_PHRASE = CONFIRM_PHRASE
PURGE_GRACE_PERIOD_SECONDS = GRACE_PERIOD_SECONDS
TICKET_CHAN_ID = TICKET_CHANNEL_ID
