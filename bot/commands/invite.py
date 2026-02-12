import datetime as dt

import discord
from discord import app_commands

from ..helpers import NO_PINGS, send_audit_embed
from ..invite_tracking import snapshot_invites_to_db
from ..db import connect


DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h
DEFAULT_MAX_USES = 0  # unlimited

INVITE_COOLDOWN_SECONDS = 5 * 60  # 5 minutes
_LAST_INVITE_AT: dict[int, dt.datetime] = {}  # user_id -> when


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _cooldown_remaining(user_id: int) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    last = _LAST_INVITE_AT.get(user_id)
    if not last:
        return 0
    elapsed = (now - last).total_seconds()
    if elapsed >= INVITE_COOLDOWN_SECONDS:
        return 0
    return int(INVITE_COOLDOWN_SECONDS - elapsed)


def setup(bot):
    @bot.tree.command(
        name="invite",
        description="Create an invite link for this channel (24h, unlimited uses).",
    )
    async def invite(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        # Cooldown (per user)
        remaining = _cooldown_remaining(interaction.user.id)
        if remaining > 0:
            await interaction.response.send_message(
                f"Slow down — you can create another invite in **{remaining}** seconds.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
            await interaction.response.send_message("I can’t create an invite for this channel type.", ephemeral=True)
            return

        me = guild.me
        if me is None:
            await interaction.response.send_message("Can't resolve bot member in this guild.", ephemeral=True)
            return

        # Only require BOT permission (users might not have it by design)
        if not channel.permissions_for(me).create_instant_invite:
            await interaction.response.send_message(
                "I don’t have permission to create invites in this channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            inv = await channel.create_invite(
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

        _LAST_INVITE_AT[interaction.user.id] = dt.datetime.now(dt.timezone.utc)

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
            f"Here’s your invite link (expires <t:{int(expires_at.timestamp())}:R>, unlimited uses):\n{inv.url}",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

        embed = discord.Embed(
            title="Invite created",
            description=(
                f"Creator: {interaction.user} ({interaction.user.id})\n"
                f"Channel: {channel.mention} ({channel.id})\n"
                f"Code: `{inv.code}`\n"
                f"Max age: {DEFAULT_MAX_AGE_SECONDS}s\n"
                f"Max uses: unlimited\n"
                f"Expires: <t:{int(expires_at.timestamp())}:R>"
            ),
        )
        await send_audit_embed(guild, embed)
