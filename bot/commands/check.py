import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import (
    NO_PINGS,
    role_ids_excluding_everyone,
    member_is_time_eligible,
    VISITOR_ROLE_ID,
    REDDITOR_ROLE_ID,
    ALLOWED_ROLE_IDS,
    DEFAULT_PURGE_DAYS,
    TICKET_CHAN_ID,
    rel_ts,
)

def setup(bot):
    @bot.tree.command(name="check", description="Staff-only: check whether a user is at risk of being purged.")
    @app_commands.describe(user="The user to check")
    async def check(interaction: discord.Interaction, user: discord.Member):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        role_ids = role_ids_excluding_everyone(user)
        has_member = VISITOR_ROLE_ID in role_ids
        has_redditor = REDDITOR_ROLE_ID in role_ids
        has_other_roles = not role_ids.issubset(ALLOWED_ROLE_IDS)

        in_scope = has_member and not has_other_roles
        days = DEFAULT_PURGE_DAYS
        time_ok = member_is_time_eligible(user, days)

        joined_str = rel_ts(user.joined_at) if user.joined_at else "unknown"

        lines: list[str] = []
        lines.append(f"**Purge check for:** {user} ({user.id})")
        lines.append(f"Joined: {joined_str}")
        lines.append("")
        lines.append("**Roles (as the bot sees them):**")
        lines.append(f"- Has Member: **{has_member}**")
        lines.append(f"- Has Redditor: **{has_redditor}**")
        lines.append(f"- Has other roles: **{has_other_roles}**")
        lines.append("")

        if not in_scope:
            lines.append("✅ **Not at risk** (not in the purge target group).")
            if not has_member:
                lines.append("- Reason: missing Member role.")
            elif has_other_roles:
                lines.append("- Reason: has roles beyond Member/Redditor.")
        else:
            if time_ok:
                lines.append("⚠️ **At risk** under default purge settings.")
                lines.append(f"- Reason: only Member (optionally Redditor) + joined > **{days}** days ago.")
                lines.append("")
                lines.append(f"Suggested action: ask them to open a ticket in <#{TICKET_CHAN_ID}>.")
            else:
                lines.append("🟡 **Not at risk yet**, but matches the role criteria.")
                lines.append(f"- Will become eligible after **{days}** days.")
                lines.append(f"If needed, direct them to <#{TICKET_CHAN_ID}>.")

        await interaction.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=NO_PINGS)
