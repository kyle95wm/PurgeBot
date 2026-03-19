import asyncio
import datetime as dt
import re

import discord
from discord.ext import commands

from .config import (
    TOKEN,
    ALLOWED_USER_IDS,
    VISITOR_ROLE_ID,
    ACTIVE_SUBSCRIBER_ROLE_ID,
    EXPIRED_ROLE_ID,
    SUBSCRIBER_ROLE_SYNC_DELAY_SECONDS,
    AUDIT_LOG_CHANNEL_ID,
)
from .views import CheckStatusPanelView
from .helpers import send_audit_embed
from .db import ensure_db
from .invite_tracking import snapshot_invites_to_db, detect_used_invite, log_join_event

# commands
from .commands import checkme, check, check_panel, list_roles, purge, bot_info, give_creds, test_purge_dm, whois, serverinfo
from .commands import invite as invite_cmd
from .commands import move_server
from .commands import move_panel
from .commands import discord_info
from .commands import afk
from .commands import server_status
from .commands import silent_ping
from .commands import remove_all_pending

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

bot.version = "modular-v1"

NEW_ACCOUNT_WARNING_DAYS = 90
NEW_ACCOUNT_WARNING_ROLE_ID = 1457561998530318478

PLEX_LINK_RE = re.compile(
    r"(?<!<)https?://(?:www\.)?plex\.tv/\S+(?!>)",
    re.IGNORECASE,
)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ensure_utc(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    if d.tzinfo is None:
        return d.replace(tzinfo=dt.timezone.utc)
    return d


def _ts_full(d: dt.datetime | None) -> str:
    d = _ensure_utc(d)
    if d is None:
        return "unknown"
    return f"<t:{int(d.timestamp())}:F>"


def _ts_rel(d: dt.datetime | None) -> str:
    d = _ensure_utc(d)
    if d is None:
        return "unknown"
    return f"<t:{int(d.timestamp())}:R>"


def _duration_text(start: dt.datetime | None, end: dt.datetime | None = None) -> str:
    start = _ensure_utc(start)
    end = _ensure_utc(end) or _utc_now()
    if start is None:
        return "unknown"

    delta = end - start
    total_seconds = max(0, int(delta.total_seconds()))

    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "<1m"


def _has_unsuppressed_plex_link(content: str) -> bool:
    return bool(PLEX_LINK_RE.search(content or ""))


async def _maybe_suppress_plex_preview(message: discord.Message) -> None:
    if message.guild is None:
        return
    if message.author.bot:
        return
    if not _has_unsuppressed_plex_link(message.content):
        return
    if message.flags.suppress_embeds:
        return

    await asyncio.sleep(2)

    try:
        refreshed = await message.channel.fetch_message(message.id)
    except Exception:
        return

    if refreshed.flags.suppress_embeds:
        return
    if not _has_unsuppressed_plex_link(refreshed.content):
        return

    try:
        await refreshed.edit(suppress=True)
    except discord.Forbidden:
        print(f"[plex-preview] Missing permissions to suppress embeds in guild {message.guild.id}")
    except discord.HTTPException as e:
        print(f"[plex-preview] Failed to suppress embed in guild {message.guild.id}: {type(e).__name__}: {e}")


async def _maybe_delete_pin_system_message(message: discord.Message) -> None:
    if message.guild is None:
        return
    if message.type != discord.MessageType.pins_add:
        return

    try:
        await message.delete()
    except discord.Forbidden:
        print(f"[pins] Missing permissions to delete pin system message in guild {message.guild.id}")
    except discord.HTTPException as e:
        print(f"[pins] Failed to delete pin system message in guild {message.guild.id}: {type(e).__name__}: {e}")


async def _send_new_account_warning_ping(guild: discord.Guild, embed: discord.Embed) -> None:
    """
    Send the new-account warning with a role ping above the embed, if the audit channel exists.
    Falls back to the normal audit helper if we can't send directly.
    """
    if not AUDIT_LOG_CHANNEL_ID:
        await send_audit_embed(guild, embed)
        return

    channel = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if channel is None:
        try:
            fetched = await guild.fetch_channel(AUDIT_LOG_CHANNEL_ID)
            channel = fetched
        except Exception:
            channel = None

    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(
                content=f"<@&{NEW_ACCOUNT_WARNING_ROLE_ID}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            return
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

    await send_audit_embed(guild, embed)


async def _sync_subscriber_roles(member: discord.Member, *, active_should_exist: bool) -> None:
    """
    After a short delay, enforce:
      - if Active Subscriber exists -> remove Expired
      - if Active Subscriber does not exist -> add Expired
    Re-checks member roles after the delay before changing anything.
    """
    if member.bot:
        return

    if not ACTIVE_SUBSCRIBER_ROLE_ID or not EXPIRED_ROLE_ID:
        return

    await asyncio.sleep(SUBSCRIBER_ROLE_SYNC_DELAY_SECONDS)

    guild = member.guild
    refreshed = guild.get_member(member.id)
    if refreshed is None:
        try:
            refreshed = await guild.fetch_member(member.id)
        except Exception:
            return

    active_role = guild.get_role(ACTIVE_SUBSCRIBER_ROLE_ID)
    expired_role = guild.get_role(EXPIRED_ROLE_ID)

    if active_role is None or expired_role is None:
        print(
            f"[subscriber-role-sync] Missing role(s) in guild {guild.id}: "
            f"active={ACTIVE_SUBSCRIBER_ROLE_ID} expired={EXPIRED_ROLE_ID}"
        )
        return

    role_ids = {r.id for r in refreshed.roles}
    has_active = ACTIVE_SUBSCRIBER_ROLE_ID in role_ids
    has_expired = EXPIRED_ROLE_ID in role_ids

    try:
        if active_should_exist:
            if has_active and has_expired:
                await refreshed.remove_roles(expired_role, reason="Active Subscriber gained; removing Expired")
        else:
            if not has_active and not has_expired:
                await refreshed.add_roles(expired_role, reason="Active Subscriber lost; adding Expired")
    except discord.Forbidden:
        print(
            f"[subscriber-role-sync] Missing permissions / hierarchy issue in guild {guild.id} "
            f"for member {refreshed.id}"
        )
    except Exception as e:
        print(f"[subscriber-role-sync] Failed in guild {guild.id} for member {refreshed.id}: {type(e).__name__}: {e}")


@bot.event
async def on_ready():
    if not hasattr(bot, "started_at") or bot.started_at is None:
        bot.started_at = dt.datetime.now(dt.timezone.utc)

    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Allowed user IDs: {sorted(ALLOWED_USER_IDS)}")

    bot.add_view(CheckStatusPanelView())

    # Persistent view for move_server staff buttons
    bot.add_view(move_server.MoveServerActionView())
    bot.add_view(move_panel.MovePanelView())

    await ensure_db()

    for g in bot.guilds:
        try:
            await snapshot_invites_to_db(g)
        except discord.Forbidden:
            print(f"[invite-tracking] Missing permissions to read invites in guild {g.id}")
        except Exception as e:
            print(f"[invite-tracking] Snapshot failed in guild {g.id}: {type(e).__name__}: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print("Command sync failed:", e)


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    # Auto-assign Member role
    try:
        role = guild.get_role(VISITOR_ROLE_ID)
        if role is None:
            print(f"[auto-role] Role {VISITOR_ROLE_ID} not found in guild {guild.id}")
        else:
            await member.add_roles(role, reason="Auto-assign Member role on join")
    except discord.Forbidden:
        print(f"[auto-role] Missing permissions / role hierarchy to assign {VISITOR_ROLE_ID} in guild {guild.id}")
    except Exception as e:
        print(f"[auto-role] Failed to assign role: {type(e).__name__}: {e}")

    # Invite tracking + logging
    invite_info = None
    unknown_reason = None

    try:
        invite_info = await detect_used_invite(guild)
        if invite_info is None:
            unknown_reason = "unknown (no invite delta detected — vanity/expired/race)"
    except discord.Forbidden:
        unknown_reason = "unknown (missing permission to read invites — give bot Manage Server)"
    except Exception as e:
        unknown_reason = f"unknown (invite check error: {type(e).__name__})"

    try:
        await log_join_event(guild_id=guild.id, member=member, invite_info=invite_info)
    except Exception as e:
        print(f"[invite-tracking] Failed to log join: {type(e).__name__}: {e}")

    # Account age check
    created_at = _ensure_utc(member.created_at)
    is_new_account = False
    if created_at is not None:
        account_age = _utc_now() - created_at
        is_new_account = account_age <= dt.timedelta(days=NEW_ACCOUNT_WARNING_DAYS)

    # Standard join log
    embed = discord.Embed(
        title="Member joined",
        description=f"{member} ({member.id}) joined.",
        color=discord.Color.green(),
    )

    embed.add_field(
        name="Account created",
        value=f"{_ts_full(created_at)}\n({_ts_rel(created_at)})",
        inline=False,
    )

    if invite_info:
        inviter = f"<@{invite_info['inviter_id']}>" if invite_info.get("inviter_id") else "unknown"
        embed.add_field(name="Invite", value=f"`{invite_info['code']}`", inline=True)
        embed.add_field(name="Inviter", value=inviter, inline=True)
        embed.add_field(name="Uses", value=f"{invite_info['before']} → {invite_info['after']}", inline=True)
    else:
        embed.add_field(name="Invite", value=unknown_reason or "unknown", inline=False)

    if is_new_account:
        embed.add_field(
            name="Account age warning",
            value=f"⚠️ Account is {NEW_ACCOUNT_WARNING_DAYS} days old or less.",
            inline=False,
        )

    await send_audit_embed(guild, embed)

    # Separate caution warning for new accounts
    if is_new_account:
        warning = discord.Embed(
            title="New account join warning",
            description=(
                f"{member.mention} ({member} / {member.id}) joined with a recently created account.\n"
                f"Please use caution."
            ),
            color=discord.Color.orange(),
        )
        warning.add_field(
            name="Account created",
            value=f"{_ts_full(created_at)}\n({_ts_rel(created_at)})",
            inline=False,
        )
        warning.add_field(
            name="Threshold",
            value=f"{NEW_ACCOUNT_WARNING_DAYS} days or less",
            inline=False,
        )
        await _send_new_account_warning_ping(guild, warning)


@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    joined_at = _ensure_utc(member.joined_at)
    created_at = _ensure_utc(member.created_at)

    embed = discord.Embed(
        title="Member left",
        description=f"{member} ({member.id}) left.",
        color=discord.Color.red(),
    )

    embed.add_field(
        name="Joined server",
        value=f"{_ts_full(joined_at)}\n({_ts_rel(joined_at)})",
        inline=False,
    )
    embed.add_field(
        name="Time in server",
        value=_duration_text(joined_at),
        inline=False,
    )
    embed.add_field(
        name="Account created",
        value=f"{_ts_full(created_at)}\n({_ts_rel(created_at)})",
        inline=False,
    )

    await send_audit_embed(guild, embed)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.bot or after.bot:
        return

    if not ACTIVE_SUBSCRIBER_ROLE_ID or not EXPIRED_ROLE_ID:
        return

    before_role_ids = {r.id for r in before.roles}
    after_role_ids = {r.id for r in after.roles}

    had_active = ACTIVE_SUBSCRIBER_ROLE_ID in before_role_ids
    has_active = ACTIVE_SUBSCRIBER_ROLE_ID in after_role_ids

    # Active Subscriber was added
    if not had_active and has_active:
        asyncio.create_task(_sync_subscriber_roles(after, active_should_exist=True))
        return

    # Active Subscriber was removed
    if had_active and not has_active:
        asyncio.create_task(_sync_subscriber_roles(after, active_should_exist=False))
        return


@bot.event
async def on_message(message: discord.Message):
    if message.type == discord.MessageType.pins_add:
        await _maybe_delete_pin_system_message(message)
        return

    asyncio.create_task(_maybe_suppress_plex_preview(message))


def load_commands():
    checkme.setup(bot)
    check.setup(bot)
    check_panel.setup(bot)
    list_roles.setup(bot)
    purge.setup(bot)
    bot_info.setup(bot)
    give_creds.setup(bot)
    test_purge_dm.setup(bot)
    invite_cmd.setup(bot)
    whois.setup(bot)
    serverinfo.setup(bot)
    discord_info.setup(bot)
    move_server.setup(bot)
    move_panel.setup(bot)
    remove_all_pending.setup(bot)
    afk.setup(bot)
    server_status.setup(bot)
    silent_ping.setup(bot)


load_commands()
bot.run(TOKEN)
