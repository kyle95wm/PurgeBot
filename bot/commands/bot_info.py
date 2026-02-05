import datetime as dt
import time
import discord

from ..config import (
    ALLOWED_USER_IDS,
    AUDIT_LOG_CHANNEL_ID,
    TICKET_CHANNEL_ID,
    DEFAULT_PURGE_DAYS,
    CONFIRM_CODE_TTL_SECONDS,
    KICK_DELAY_SECONDS,
    GRACE_PERIOD_SECONDS,
    VISITOR_ROLE_ID,
    REDDITOR_ROLE_ID,
)
from ..helpers import NO_PINGS


def _fmt_uptime(started_at: dt.datetime | None) -> str:
    if not started_at:
        return "unknown"
    now = dt.datetime.now(dt.timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=dt.timezone.utc)
    delta = now - started_at

    total = int(delta.total_seconds())
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def setup(bot):
    @bot.tree.command(
        name="bot_info",
        description="Staff-only: show current bot settings + runtime info.",
    )
    async def bot_info(interaction: discord.Interaction):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        started_at = getattr(bot, "started_at", None)
        version = getattr(bot, "version", "unknown")

        # "Ping" bits:
        gateway_ms = int(getattr(bot, "latency", 0.0) * 1000)
        t0 = time.perf_counter()

        embed = discord.Embed(title="Bot Info")

        embed.add_field(name="Version", value=str(version), inline=True)
        embed.add_field(name="Uptime", value=_fmt_uptime(started_at), inline=True)
        embed.add_field(name="Entrypoint", value="python -m bot.main", inline=False)

        embed.add_field(
            name="Latency",
            value=f"- Gateway: **{gateway_ms}ms**\n- Interaction: *(measuring...)*",
            inline=False,
        )

        embed.add_field(
            name="Purge defaults",
            value=(
                f"- Days: **{DEFAULT_PURGE_DAYS}**\n"
                f"- Grace: **{GRACE_PERIOD_SECONDS}s**\n"
                f"- Confirm TTL: **{CONFIRM_CODE_TTL_SECONDS//60} min**\n"
                f"- Kick delay: **{KICK_DELAY_SECONDS:.1f}s**"
            ),
            inline=False,
        )

        embed.add_field(
            name="Role logic (target group)",
            value=(
                "- Requires **Member** role\n"
                "- **Redditor** is optional (depending on role_mode)\n"
                "- Must have **no other roles** besides Member/Redditor (+ @everyone)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Configured IDs",
            value=(
                f"- Member role ID: `{VISITOR_ROLE_ID}`\n"
                f"- Redditor role ID: `{REDDITOR_ROLE_ID}`\n"
                f"- Ticket channel: <#{TICKET_CHANNEL_ID}>\n"
                f"- Audit log: " + (f"<#{AUDIT_LOG_CHANNEL_ID}>" if AUDIT_LOG_CHANNEL_ID else "**disabled**")
            ),
            inline=False,
        )

        allowed_preview = "\n".join(f"- `{x}`" for x in sorted(ALLOWED_USER_IDS))
        embed.add_field(
            name="Access control",
            value=(
                "Staff-only commands:\n"
                "- `/purge_eligible`\n"
                "- `/list_only_allowed_roles`\n"
                "- `/check`\n"
                "- `/check_panel`\n"
                "- `/bot_info`\n\n"
                f"Allowed user IDs:\n{allowed_preview}"
            ),
            inline=False,
        )

        embed.add_field(
            name="User tools",
            value=(
                "- `/checkme` (cooldown enforced)\n"
                "- Panel button: **Check my status** (persistent)\n"
                "- Panel link: **Open a ticket**"
            ),
            inline=False,
        )

        # Send once, then edit to include interaction RTT.
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

        rtt_ms = int((time.perf_counter() - t0) * 1000)
        embed.set_field_at(
            3,  # "Latency" field index (0-based): Version/Uptime/Entrypoint/Latency
            name="Latency",
            value=f"- Gateway: **{gateway_ms}ms**\n- Interaction: **{rtt_ms}ms**",
            inline=False,
        )
        await interaction.edit_original_response(embed=embed)
