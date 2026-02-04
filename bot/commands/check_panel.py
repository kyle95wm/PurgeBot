import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS
from ..views import CheckStatusPanelView

def setup(bot):
    @bot.tree.command(
        name="check_panel",
        description="Staff-only: post a 'check my status' panel with a ticket link.",
    )
    @app_commands.describe(channel="Where to post the status-check panel")
    async def check_panel(interaction: discord.Interaction, channel: discord.TextChannel):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Purge Status Check",
            description=(
                "Click **Check my status** to privately see if you’re at risk of being purged.\n"
                "If something looks wrong, use **Open a ticket** to contact staff."
            ),
        )

        await channel.send(
            embed=embed,
            view=CheckStatusPanelView(guild_id=guild.id),
            allowed_mentions=NO_PINGS,
        )
        await interaction.response.send_message(f"Posted the panel in {channel.mention}.", ephemeral=True)
