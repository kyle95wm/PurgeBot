import discord
from discord import app_commands

from ..helpers import NO_PINGS


def setup(bot):
    @bot.tree.command(
        name="discord_info",
        description="Generate Discord ID + username instructions for a user.",
    )
    @app_commands.describe(user="The user to generate Discord info for.")
    async def discord_info(interaction: discord.Interaction, user: discord.User):
        username = str(user)
        user_id = user.id

        message = (
            "You can subscribe via our website. The instructions are below.\n\n"
            f"When signing up, please fill in your Discord ID, which is **{user_id}**, "
            f"and your Discord username, which is **{username}**.\n\n"
            "This ensures you get the correct server roles after you subscribe."
        )

        await interaction.response.send_message(
            message,
            allowed_mentions=NO_PINGS,
        )
