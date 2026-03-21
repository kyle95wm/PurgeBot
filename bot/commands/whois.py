import datetime as dt

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, rel_ts
from ..db import connect


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


def _parse_iso_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        return None


async def _get_invite_join_info(*, guild_id: int, member_id: int) -> dict | None:
    async with connect() as db:
        row = await db.execute_fetchone(
            """
            SELECT invite_code, inviter_id, uses_before, uses_after, joined_at
            FROM invite_join_log
            WHERE guild_id = ? AND member_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (guild_id, member_id),
        )

    if not row:
        return None

    invite_code, inviter_id, uses_before, uses_after, joined_at = row
    return {
        "invite_code": invite_code,
        "inviter_id": inviter_id,
        "uses_before": uses_before,
        "uses_after": uses_after,
        "joined_at": _parse_iso_dt(joined_at),
    }


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
        roles_sorted = sorted(roles, key=lambda r: r.position, reverse=True)
        roles_text = " ".join(r.mention for r in roles_sorted) if roles_sorted else "(none)"

        joined_at = user.joined_at
        created_at = user.created_at

        invite_info = None
        try:
            invite_info = await _get_invite_join_info(guild_id=guild.id, member_id=user.id)
        except Exception:
            invite_info = None

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

        if invite_info:
            inviter_text = (
                f"<@{invite_info['inviter_id']}> (`{invite_info['inviter_id']}`)"
                if invite_info.get("inviter_id")
                else "unknown"
            )
            invite_code = invite_info.get("invite_code") or "unknown"
            uses_before = invite_info.get("uses_before")
            uses_after = invite_info.get("uses_after")
            logged_joined_at = invite_info.get("joined_at")

            uses_text = "unknown"
            if uses_before is not None and uses_after is not None:
                uses_text = f"{uses_before} → {uses_after}"

            invite_value = (
                f"Code: `{invite_code}`\n"
                f"Inviter: {inviter_text}\n"
                f"Uses: {uses_text}\n"
                f"Logged join: {rel_ts(logged_joined_at)}"
            )
        else:
            invite_value = "No invite tracking record found."

        embed.add_field(name="Invite tracking", value=invite_value[:1024], inline=False)

        embed.add_field(name=f"Roles ({len(roles_sorted)})", value=roles_text[:1024], inline=False)

        if roles_sorted:
            embed.set_footer(text=f"Top role: {roles_sorted[0].name}")

        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)
