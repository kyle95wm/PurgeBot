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
    XC_URL,
    PURGE_DM_ENABLED,
    PURGE_DM_TEMPLATE,
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
            name="Highlights",
            value=(
                "- Purge flow with dry-run preview, confirm code, DM option, and grace cancel\n"
                "- AFK system with optional ETA, auto-clear on return, and mention/reply notices\n"
                "- Announcement flow with modal, ephemeral preview, optional role pings, and embed mode\n"
                "- Move-server requests with destination picker, modal submit, and staff approve/deny\n"
                "- Invite tracking stored in sqlite and exposed in `/whois`\n"
                "- Auto-assigns **Member** on join and syncs Active Subscriber/Expired roles\n"
                "- Suppresses Plex link previews and deletes pin system messages"
            ),
            inline=False,
        )

        purge_dm_status = "enabled" if (PURGE_DM_ENABLED and PURGE_DM_TEMPLATE) else "disabled"
        embed.add_field(
            name="Purge defaults",
            value=(
                f"- Days: **{DEFAULT_PURGE_DAYS}**\n"
                f"- Grace: **{GRACE_PERIOD_SECONDS}s**\n"
                f"- Confirm TTL: **{CONFIRM_CODE_TTL_SECONDS//60} min**\n"
                f"- Kick delay: **{KICK_DELAY_SECONDS:.1f}s**\n"
                f"- Purge DM: **{purge_dm_status}**\n"
                "- Role modes: **both**, **redditor_only**, **member_only**, **expired_only**"
            ),
            inline=False,
        )

        embed.add_field(
            name="Role logic",
            value=(
                "- Standard purge targets require **Member** and no extra roles beyond Member/Redditor\n"
                "- `expired_only` targets members with **Expired** unless they also have the exemption role\n"
                "- `/move_server` uses the configured server roles (**Omega**, **Alpha**, **Delta**)\n"
                "- `/server_status` can open/close move destinations with an optional staff note"
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

        # Keep XC URL visible for staff sanity-checking (it’s already in .env)
        embed.add_field(
            name="XC",
            value=f"- XC URL: <{XC_URL}>",
            inline=False,
        )

        allowed_preview = "\n".join(f"- `{x}`" for x in sorted(ALLOWED_USER_IDS)) or "(none configured)"
        embed.add_field(
            name="Staff tools",
            value=(
                "Slash commands:\n"
                "- `/announce`, `/bot_info`, `/check`, `/check_panel`\n"
                "- `/give_creds`, `/extend_creds`, `/test_purge_dm`\n"
                "- `/list_only_allowed_roles`, `/purge_eligible`, `/remove_all_pending`\n"
                "- `/move_panel`, `/silent_ping`, `/whois`, `/afk_clear`\n"
                "- `/server_status set`, `/server_status clear`, `/server_status list`\n\n"
                "Limited staff path:\n"
                "- `/invite user:<member>` allows invite creation on behalf of someone else\n\n"
                "Allowed user IDs:\n"
                f"{allowed_preview}"
            ),
            inline=False,
        )

        embed.add_field(
            name="General tools",
            value=(
                "- `/afk` to set AFK with optional ETA/note\n"
                "- `/checkme` to self-check purge risk\n"
                "- `/discord_info` to generate Discord signup details\n"
                "- `/invite` to create or reuse your own 24h landing-channel invite\n"
                "- `/move_server` to request a move between open destinations\n"
                "- `/serverinfo` for server stats (ephemeral by default)\n"
                "- Context menu: **Whois** for staff"
            ),
            inline=False,
        )

        embed.add_field(
            name="Panels and logging",
            value=(
                "- `/check_panel` posts the persistent purge self-check panel\n"
                "- `/move_panel` posts the persistent move-request panel\n"
                "- Join/leave events and staff actions log to the audit channel when configured\n"
                "- `/whois` includes stored invite-tracking details when available"
            ),
            inline=False,
        )

        embed.add_field(
            name="Access control",
            value=(
                "- `/purge_eligible`\n"
                "- Staff-only slash commands are gated by `ALLOWED_USER_IDS`\n"
                "- Some actions also require Discord perms, like Kick Members or Manage Roles\n"
                "- User-facing commands still depend on server/channel context and bot permissions"
            ),
            inline=False,
        )

        # Send once, then edit to include interaction RTT.
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PINGS)

        rtt_ms = int((time.perf_counter() - t0) * 1000)
        embed.set_field_at(
            3,  # Latency field index (0-based): Version/Uptime/Entrypoint/Latency
            name="Latency",
            value=f"- Gateway: **{gateway_ms}ms**\n- Interaction: **{rtt_ms}ms**",
            inline=False,
        )
        await interaction.edit_original_response(embed=embed)
