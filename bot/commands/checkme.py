import discord
from ..helpers import NO_PINGS, checkme_on_cooldown, mark_checkme_used, build_checkme_message

def setup(bot):
    @bot.tree.command(name="checkme", description="Check your purge risk status.")
    async def checkme(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        on_cd, remaining = checkme_on_cooldown(interaction.user.id)
        if on_cd:
            minutes = (remaining + 59) // 60
            await interaction.response.send_message(
                f"You can use this again in **{minutes}** minute(s).",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        mark_checkme_used(interaction.user.id)

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = await guild.fetch_member(interaction.user.id)

        await interaction.response.send_message(
            build_checkme_message(member),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
