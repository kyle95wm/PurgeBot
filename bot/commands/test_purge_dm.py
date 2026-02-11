import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS, DEFAULT_PURGE_DAYS, PURGE_DM_ENABLED, PURGE_DM_TEMPLATE
from ..helpers import NO_PINGS, RoleMode, pretty_role_mode


def _render_purge_dm(*, member: discord.Member, guild: discord.Guild, days: int, role_mode: str) -> str:
    # Same placeholders as purge:
    # {user}, {server}, {days}, {role_mode}
    return (
        PURGE_DM_TEMPLATE
        .replace("{user}", str(member))
        .replace("{server}", guild.name)
        .replace("{days}", str(days))
        .replace("{role_mode}", str(role_mode))
    )


def setup(bot):
    @bot.tree.command(
        name="test_purge_dm",
        description="Staff-only: send a test purge DM to a user (no kick).",
    )
    @app_commands.describe(
        user="User to DM",
        days="Value used for {days} placeholder (default: bot default purge days)",
        role_mode="Value used for {role_mode} placeholder (default: both)",
    )
    async def test_purge_dm(
        interaction: discord.Interaction,
        user: discord.Member,
        days: int = DEFAULT_PURGE_DAYS,
        role_mode: RoleMode = "both",
    ):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        if not PURGE_DM_ENABLED:
            await interaction.response.send_message(
                "Purge DM is currently **disabled** (`PURGE_DM_ENABLED=false`).",
                ephemeral=True,
            )
            return

        if not PURGE_DM_TEMPLATE:
            await interaction.response.send_message(
                "Purge DM is enabled, but `PURGE_DM_TEMPLATE` is empty.",
                ephemeral=True,
            )
            return

        if days < 1:
            await interaction.response.send_message("Set days to 1 or higher.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        msg = _render_purge_dm(member=user, guild=guild, days=days, role_mode=str(role_mode))

        try:
            await user.send(msg, allowed_mentions=NO_PINGS)
            await interaction.followup.send(
                "✅ DM sent.\n"
                f"- User: {user} ({user.id})\n"
                f"- days: {days}\n"
                f"- role_mode: {pretty_role_mode(role_mode)}\n\n"
                "Preview:\n"
                f"```{msg}```",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ DM failed (Forbidden). They likely have DMs closed / blocked.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ DM failed ({type(e).__name__}).",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
