import datetime as dt

import discord
from discord import app_commands

from ..helpers import NO_PINGS, send_audit_embed
from ..invite_tracking import snapshot_invites_to_db


DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h
DEFAULT_MAX_USES = 0  # 0 = unlimited uses


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

        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)):
            await interaction.response.send_message("I can’t create an invite for this channel type.", ephemeral=True)
            return

        me = guild.me
        if me is None:
            await interaction.response.send_message("Can't resolve bot member in this guild.", ephemeral=True)
            return

        # Permission checks
        if not channel.permissions_for(me).create_instant_invite:
            await interaction.response.send_message(
                "I don’t have permission to create invites in this channel.",
                ephemeral=True,
            )
            return

        if not channel.permissions_for(interaction.user).create_instant_invite:
            await interaction.response.send_message(
                "You don’t have permission to create invites in this channel.",
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

        # Snapshot right after creating the invite so the baseline knows it exists
        try:
            await snapshot_invites_to_db(guild)
        except Exception:
            pass

        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=DEFAULT_MAX_AGE_SECONDS)

        await interaction.followup.send(
            f"Here’s your invite link (expires <t:{int(expires_at.timestamp())}:R>, unlimited uses):\n{inv.url}",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

        # Audit log
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
