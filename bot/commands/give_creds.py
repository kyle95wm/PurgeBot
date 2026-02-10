import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS, format_creds_message
from ..helpers import NO_PINGS

# Only allow user mentions (no @everyone/@roles)
USER_PINGS_ONLY = discord.AllowedMentions(users=True, roles=False, everyone=False)


def setup(bot):
    @bot.tree.command(
        name="give_creds",
        description="Staff-only: post + DM a user their XC credentials.",
    )
    @app_commands.describe(
        user="The member to receive the credentials",
        username="XC username (plain text)",
        password="XC password (case sensitive)",
        expiry="Expiration date (plain text, e.g. 2026-03-01)",
    )
    async def give_creds(
        interaction: discord.Interaction,
        user: discord.Member,
        username: str,
        password: str,
        expiry: str,
    ):
        # Staff lock
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            await interaction.response.send_message("Run this in a server channel, not DMs.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        details = format_creds_message(username=username, password=password, expiry=expiry)

        # 1) Post publicly in the ticket channel + ping user
        channel_ok = True
        try:
            await channel.send(
                content=f"{user.mention}\n\n{details}",
                allowed_mentions=USER_PINGS_ONLY,
            )
        except Exception:
            channel_ok = False

        # 2) DM the same details
        dm_ok = True
        dm_error = None
        try:
            await user.send(details, allowed_mentions=NO_PINGS)
        except discord.Forbidden:
            dm_ok = False
            dm_error = "DMs are closed / blocked."
        except Exception as e:
            dm_ok = False
            dm_error = f"DM failed: {type(e).__name__}"

        # 3) Ephemeral confirmation to staff (invoker)
        if channel_ok and dm_ok:
            msg = "Posted in-channel and sent DM."
        elif channel_ok and not dm_ok:
            msg = "Posted in-channel, but DM failed."
        elif (not channel_ok) and dm_ok:
            msg = "DM sent, but posting in-channel failed."
        else:
            msg = "Both posting in-channel and DM failed."

        if dm_error:
            msg += f"\nDM error: {dm_error}"

        await interaction.followup.send(msg, ephemeral=True, allowed_mentions=NO_PINGS)
