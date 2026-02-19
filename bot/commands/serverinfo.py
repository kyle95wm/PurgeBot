import datetime as dt

import discord

from ..helpers import NO_PINGS


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


async def _count_humans_bots(guild: discord.Guild) -> tuple[int, int]:
    humans = 0
    bots = 0
    async for m in guild.fetch_members(limit=None):
        if m.bot:
            bots += 1
        else:
            humans += 1
    return humans, bots


def setup(bot):
    @bot.tree.command(
        name="serverinfo",
        description="Show server info + member breakdown (humans vs bots).",
    )
    async def serverinfo(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=false)
            return

        await interaction.response.defer(ephemeral=false)

        # Member breakdown (requires Members intent + ability to fetch members)
        humans = bots = 0
        breakdown_note = None
        try:
            humans, bots = await _count_humans_bots(guild)
        except discord.Forbidden:
            breakdown_note = "Couldn’t scan members (missing permissions)."
        except Exception:
            breakdown_note = "Couldn’t scan members (unexpected error)."

        owner = guild.owner
        created = guild.created_at

        embed = discord.Embed(title="Server Info")
        embed.add_field(name="Name", value=guild.name, inline=False)
        embed.add_field(name="ID", value=str(guild.id), inline=True)
        embed.add_field(
            name="Owner",
            value=(f"{owner} ({owner.id})" if owner else "unknown"),
            inline=True,
        )

        embed.add_field(
            name="Created",
            value=f"{_abs_ts(created)}\n({_rel_ts(created)})",
            inline=False,
        )

        # Counts
        embed.add_field(
            name="Members",
            value=(
                f"Total (Discord): **{guild.member_count or 'unknown'}**\n"
                f"Humans: **{humans}**\n"
                f"Bots: **{bots}**"
            ),
            inline=False,
        )
        if breakdown_note:
            embed.add_field(name="Note", value=breakdown_note, inline=False)

        # Extras (safe + useful)
        embed.add_field(name="Boost level", value=str(guild.premium_tier), inline=True)
        embed.add_field(name="Boosts", value=str(guild.premium_subscription_count or 0), inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level), inline=True)

        # Icon
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        await interaction.followup.send(embed=embed, ephemeral=false, allowed_mentions=NO_PINGS)
