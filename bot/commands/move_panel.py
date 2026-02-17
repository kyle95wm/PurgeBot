import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS
from . import move_server


PANEL_TITLE = "Server Move Request"
PANEL_BODY = (
    "Need to move between East, West, or South?\n\n"
    "Click the button below to begin your server move request."
)


class MovePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request Server Move",
        style=discord.ButtonStyle.primary,
        custom_id="move_panel:open",
    )
    async def open_move(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Reuse the actual /move_server logic
        await move_server.setup(interaction.client)  # safety (no-op if already loaded)

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        # Just call the same slash command handler logic
        # Instead of duplicating code, trigger the command function directly
        command = interaction.client.tree.get_command("move_server")
        if command:
            await command._callback(interaction)  # reuse the slash command callback


def setup(bot):
    @bot.tree.command(
        name="move_panel",
        description="Staff-only: create a server move request panel in this channel.",
    )
    async def move_panel(interaction: discord.Interaction):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        embed = discord.Embed(
            title=PANEL_TITLE,
            description=PANEL_BODY,
            color=discord.Color.from_str("#a9c9ff"),
        )

        await interaction.response.send_message(
            embed=embed,
            view=MovePanelView(),
            allowed_mentions=NO_PINGS,
        )
