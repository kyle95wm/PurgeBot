import datetime as dt

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, rel_ts


def _age_str(since: dt.datetime | None) -> str:
    if not since:
        return "unknown"
    if since.tzinfo is None:
        since = since.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    delta = now - since

    days = delta.days
    hours = (delta.seconds // 3600)
    mins = (delta.seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def setup(bot):
    @bot.tree.command(
        name="whois",
        description="Staff: show server info about a member (roles, join age, account age, etc).",
    )
    @app_commands.describe(user="Member to look up")
    async def whois(interaction: discord.Interaction, user: discord.Member):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        # Roles (exclude @everyone)
        roles = [r for r in user.roles if r != guild.default_role]
        # Show highest -> lowest
        roles_sorted = sorted(roles, key=lambda r: r.position, reverse=True)

        roles_text = " ".join(r.mention for r in roles_sorted) if roles_sorted else "(none)"

        joined_at = user.joined_at
        created_at = user.created_at  # always present for discord.Member

        embed = discord.Embed(
            title="Whois",
            description=f"{user.mention} ({user})",
        )

        embed.add_field(name="User ID", value=str(user.id), inline=True)
        embed.add_field(name="Bot?", value=str(bool(user.bot)), inline=True)
        embed.add_field(name="Nickname", value=(user.nick or "(none)"), inline=True)

        embed.add_field(
            name="Server joined",
            value=f"{rel_ts(joined_at)}\nAge: **{_age_str(joined_at)}**",
            inline=False,
        )
        embed.add_field(
            name="Account created",
            value=f"{rel_ts(created_at)}\nAge: **{_age_str(created_at)}**",
            inline=False,
        )

        embed.add_field(name=f"Roles ({len(roles_sorted)})", value=roles_text[:1024], inline=False)

        # Nice-to-have: show highest role
        if roles_sorted:
            embed.set_footer(text=f"Top role: {roles_sorted[0].name}")

        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)
