import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS

# Reuse the existing move_server flow + config
from . import move_server


PANEL_TITLE = "Move Server Requests"
PANEL_BODY = (
    "Use the button below to request a server move.\n"
    "You’ll pick your destination, then submit your email + reason.\n\n"
    "If you don’t receive a DM after staff handles it, check the fallback channel and/or open a ticket."
)

PANEL_COLOR = 0xA9C9FF


async def _start_move_flow(interaction: discord.Interaction) -> None:
    """Same behavior as running /move_server."""
    guild = interaction.guild
    user = interaction.user

    if guild is None or not isinstance(user, discord.Member):
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return

    # Cooldown check
    on_cd, remaining = move_server._check_cooldown(user.id)  # type: ignore
    if on_cd:
        mins = max(1, remaining // 60)
        await interaction.response.send_message(
            f"You can submit another request in {mins} minute(s).",
            ephemeral=True,
        )
        return

    current_role_id = move_server._get_current_server_role(user)  # type: ignore
    if current_role_id is None:
        allowed = "\n".join(f"- {name}" for name in move_server.SERVER_ROLES.values())  # type: ignore
        await interaction.response.send_message(
            "You must have exactly one server role to use this:\n" + allowed,
            ephemeral=True,
        )
        return

    dest_ids = move_server._allowed_destinations(current_role_id)  # type: ignore
    if not dest_ids:
        await interaction.response.send_message("No destinations available.", ephemeral=True)
        return

    source_channel_id = interaction.channel.id if interaction.channel else 0
    from_name = move_server.SERVER_ROLES.get(current_role_id, str(current_role_id))  # type: ignore

    view = move_server.MoveServerDestinationView(  # type: ignore
        author_id=user.id,
        source_channel_id=source_channel_id,
        from_role_id=current_role_id,
        destination_role_ids=dest_ids,
    )

    await interaction.response.send_message(
        content=f"Current server: **{from_name}**\nPick where you want to move to:",
        view=view,
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


class MovePanelView(discord.ui.View):
    """
    Persistent view so the button keeps working after restarts.
    Requirements:
      - timeout=None
      - custom_id on the button
    """
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request a server move",
        style=discord.ButtonStyle.primary,
        custom_id="move_panel:open",
    )
    async def open_move(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_move_flow(interaction)


def setup(bot):
    @bot.tree.command(
        name="move_panel",
        description="Staff-only: post a Move Server panel with a button users can click.",
    )
    @app_commands.describe(channel="Channel to post the panel in (defaults to current channel).")
    async def move_panel(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Pick a text channel to post the panel in.", ephemeral=True)
            return

        embed = discord.Embed(title=PANEL_TITLE, description=PANEL_BODY, color=PANEL_COLOR)

        try:
            msg = await target.send(embed=embed, view=MovePanelView(), allowed_mentions=NO_PINGS)
        except discord.Forbidden:
            await interaction.response.send_message("I can’t post in that channel (missing perms).", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Failed to post the panel (Discord API error).", ephemeral=True)
            return

        await interaction.response.send_message(f"Posted panel: {msg.jump_url}", ephemeral=True, allowed_mentions=NO_PINGS)
