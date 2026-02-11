import datetime as dt
import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS, format_creds_message
from ..helpers import NO_PINGS

USER_PINGS_ONLY = discord.AllowedMentions(users=True, roles=False, everyone=False)

ACCEPTED_EXPIRY_FORMATS = [
    "%Y-%m-%d",   # 2026-03-01
    "%Y/%m/%d",   # 2026/03/01
    "%Y.%m.%d",   # 2026.03.01
    "%m/%d/%Y",   # 03/01/2026
    "%m-%d-%Y",   # 03-01-2026
    "%b %d %Y",   # Mar 01 2026
    "%b %d, %Y",  # Mar 01, 2026
    "%B %d %Y",   # March 01 2026
    "%B %d, %Y",  # March 01, 2026
    "%d %b %Y",   # 01 Mar 2026
    "%d %B %Y",   # 01 March 2026
]

ACCEPTED_EXPIRY_HELP = (
    "Expiry must be a date. Accepted formats:\n"
    "- YYYY-MM-DD (recommended)\n"
    "- YYYY/MM/DD\n"
    "- YYYY.MM.DD\n"
    "- MM/DD/YYYY\n"
    "- MM-DD-YYYY\n"
    "- Mar 1 2026 / March 1 2026 / 1 Mar 2026\n\n"
    "Tip: Use YYYY-MM-DD to avoid ambiguity."
)


def parse_expiry(expiry_str: str) -> dt.date | None:
    s = expiry_str.strip()
    for fmt in ACCEPTED_EXPIRY_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def setup(bot):
    @bot.tree.command(
        name="give_creds",
        description="Staff-only: post + DM a user their XC credentials.",
    )
    @app_commands.describe(
        user="The member to receive the credentials",
        username="XC username (plain text)",
        password="XC password (case sensitive)",
        expiry="Expiration date (e.g. 2026-03-01, 03/01/2026, Mar 1 2026)",
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

        parsed_expiry = parse_expiry(expiry)
        if parsed_expiry is None:
            await interaction.response.send_message(ACCEPTED_EXPIRY_HELP, ephemeral=True)
            return

        # Optional: reject past dates
        if parsed_expiry < dt.date.today():
            await interaction.response.send_message("Expiry date cannot be in the past.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        normalized_expiry = parsed_expiry.isoformat()
        details = format_creds_message(username=username, password=password, expiry=normalized_expiry)

        # 1) Post publicly in the ticket channel + ping user (with creds)
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

        # 3) Public follow-up if DM succeeded
        if dm_ok:
            try:
                await channel.send(
                    content=f"{user.mention} I also DM’d you these details for your records.",
                    allowed_mentions=USER_PINGS_ONLY,
                )
            except Exception:
                pass

        # 4) Ephemeral confirmation to staff
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
