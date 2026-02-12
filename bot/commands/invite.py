import datetime as dt

import discord
from discord import app_commands

from ..helpers import NO_PINGS, send_audit_embed
from ..invite_tracking import snapshot_invites_to_db
from ..db import connect


# Always create invites to this "landing" channel
INVITE_TARGET_CHANNEL_ID = 1457896130653458542

DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h
DEFAULT_MAX_USES = 0  # 0 = unlimited uses


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def _get_target_channel(guild: discord.Guild) -> discord.abc.GuildChannel | None:
    ch = guild.get_channel(INVITE_TARGET_CHANNEL_ID)
    if ch is None:
        try:
            ch = await guild.fetch_channel(INVITE_TARGET_CHANNEL_ID)
        except Exception:
            return None
    if isinstance(ch, discord.abc.GuildChannel):
        return ch
    return None


def setup(bot):
    @bot.tree.command(
        name="invite",
        description="Create a 24h invite (unlimited uses) to the public landing channel.",
    )
    async def invite(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        me = guild.me
        if me is None:
            await interaction.response.send_message("Can't resolve bot member in this guild.", ephemeral=True)
            return

        target = await _get_target_channel(guild)
        if target is None:
            await interaction.response.send_message(
                f"I can't find the invite target channel `{INVITE_TARGET_CHANNEL_ID}` in this server.",
                ephemeral=True,
            )
            return

        # We only need BOT permission (users might not have invite perms by design)
        if not target.permissions_for(me).create_instant_invite:
            await interaction.response.send_message(
                f"I don’t have permission to create invites in <#{INVITE_TARGET_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            inv = await target.create_invite(
                max_age=DEFAULT_MAX_AGE_SECONDS,
                max_uses=DEFAULT_MAX_USES,
                unique=True,
                reason=f"Invite created via /invite by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send("Invite creation failed (missing permissions).", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("Invite creation failed (Discord API error). Try again.", ephemeral=True)
            return

        # Snapshot so baseline knows about the invite
        try:
            await snapshot_invites_to_db(guild)
        except Exception:
            pass

        # Store “creator” as the user who ran /invite (not the bot)
        try:
            now = _now_iso()
            created_at = inv.created_at.isoformat() if inv.created_at else None
            uses = inv.uses or 0

            async with connect() as db:
                await db.execute(
                    """
                    INSERT INTO invite_baseline (guild_id, code, uses, inviter_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, code) DO UPDATE SET
                      uses=excluded.uses,
                      inviter_id=excluded.inviter_id,
                      created_at=COALESCE(invite_baseline.created_at, excluded.created_at),
                      updated_at=excluded.updated_at
                    """,
                    (guild.id, inv.code, uses, interaction.user.id, created_at, now),
                )
                await db.commit()
        except Exception:
            pass

        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=DEFAULT_MAX_AGE_SECONDS)

        await interaction.followup.send(
            f"Here’s your invite link (goes to <#{INVITE_TARGET_CHANNEL_ID}>, expires <t:{int(expires_at.timestamp())}:R>):\n{inv.url}",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

        # Audit log
        embed = discord.Embed(
            title="Invite created",
            description=(
                f"Creator: {interaction.user} ({interaction.user.id})\n"
                f"Target channel: <#{INVITE_TARGET_CHANNEL_ID}> ({INVITE_TARGET_CHANNEL_ID})\n"
                f"Code: `{inv.code}`\n"
                f"Max age: {DEFAULT_MAX_AGE_SECONDS}s\n"
                f"Max uses: unlimited\n"
                f"Expires: <t:{int(expires_at.timestamp())}:R>"
            ),
        )
        await send_audit_embed(guild, embed)
