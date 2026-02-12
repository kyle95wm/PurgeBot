import datetime as dt
import secrets

import discord

from ..config import ALLOWED_USER_IDS, TICKET_CHANNEL_ID
from ..helpers import NO_PINGS, rel_ts, send_audit_embed


# Roles that are allowed to use /move_server
ROLE_EAST_ID = 1466939252024541423  # SS-VOD East
ROLE_WEST_ID = 1466938881764233396  # SS-VOD West

# Where staff actions land
MOVE_REQUESTS_CHANNEL_ID = 1468797510897373425

# Cooldown (change to taste)
MOVE_SERVER_COOLDOWN_SECONDS = 60 * 60  # 1 hour

# In-memory cooldown tracking (resets on bot restart)
MOVE_LAST_SUBMITTED: dict[int, dt.datetime] = {}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _seconds_left(user_id: int) -> int:
    last = MOVE_LAST_SUBMITTED.get(user_id)
    if not last:
        return 0
    elapsed = (_now() - last).total_seconds()
    remaining = MOVE_SERVER_COOLDOWN_SECONDS - elapsed
    return int(remaining) if remaining > 0 else 0


def _mark_submitted(user_id: int) -> None:
    MOVE_LAST_SUBMITTED[user_id] = _now()


def _get_role_ids(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles if r != member.guild.default_role}


def _role_direction(member: discord.Member) -> tuple[str, int, str, int] | None:
    """
    Returns (from_name, from_role_id, to_name, to_role_id) or None if user isn't eligible.
    """
    role_ids = _get_role_ids(member)
    has_east = ROLE_EAST_ID in role_ids
    has_west = ROLE_WEST_ID in role_ids

    # Must have exactly one of them
    if has_east and not has_west:
        return ("SS-VOD East", ROLE_EAST_ID, "SS-VOD West", ROLE_WEST_ID)
    if has_west and not has_east:
        return ("SS-VOD West", ROLE_WEST_ID, "SS-VOD East", ROLE_EAST_ID)

    return None


async def _fetch_requests_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch = guild.get_channel(MOVE_REQUESTS_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        fetched = await guild.fetch_channel(MOVE_REQUESTS_CHANNEL_ID)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


def _new_request_id() -> str:
    return secrets.token_hex(4).upper()  # 8 chars


def _find_field(embed: discord.Embed, name: str) -> str | None:
    for f in embed.fields:
        if (f.name or "").strip().lower() == name.strip().lower():
            return f.value
    return None


def _parse_origin_channel_id(embed: discord.Embed) -> int | None:
    """
    Field value is stored like: "<#123> (123)"
    We'll try to extract the numeric ID.
    """
    v = _find_field(embed, "Origin channel")
    if not v:
        return None
    # common pattern: "...(123)"
    if "(" in v and ")" in v:
        try:
            inside = v.split("(", 1)[1].split(")", 1)[0].strip()
            return int(inside)
        except Exception:
            pass
    # fallback: raw digits anywhere
    digits = "".join(ch for ch in v if ch.isdigit())
    try:
        return int(digits) if digits else None
    except Exception:
        return None


def _parse_request_from_embed(embed: discord.Embed) -> dict | None:
    """
    Pull request data from the embed we posted to staff.
    Footer text: "Request ID: XXXXXXXX | Requester: 123"
    """
    rid = None
    requester_id = None

    if embed.footer and embed.footer.text:
        txt = embed.footer.text
        try:
            parts = [p.strip() for p in txt.split("|")]
            for p in parts:
                if p.lower().startswith("request id:"):
                    rid = p.split(":", 1)[1].strip()
                if p.lower().startswith("requester:"):
                    requester_id = int(p.split(":", 1)[1].strip())
        except Exception:
            pass

    from_to = _find_field(embed, "Move")
    if not requester_id or not from_to:
        return None

    return {
        "request_id": rid or "UNKNOWN",
        "requester_id": requester_id,
        "move": from_to,
        "origin_channel_id": _parse_origin_channel_id(embed),
    }


def _staff_ping_content() -> str:
    # Ping the allowed IDs from .env
    if not ALLOWED_USER_IDS:
        return ""
    mentions = " ".join(f"<@{uid}>" for uid in sorted(ALLOWED_USER_IDS))
    return f"New move request: {mentions}"


STAFF_PINGS_ALLOWED = discord.AllowedMentions(users=True, roles=False, everyone=False)
USER_PING_ALLOWED = discord.AllowedMentions(users=True, roles=False, everyone=False)


async def _notify_origin_channel_if_needed(
    *,
    guild: discord.Guild,
    origin_channel_id: int | None,
    requester_id: int,
    text: str,
) -> bool:
    """
    Best-effort. Returns True if sent, False otherwise.
    """
    if not origin_channel_id:
        return False

    ch = guild.get_channel(origin_channel_id)
    if ch is None:
        try:
            ch = await guild.fetch_channel(origin_channel_id)
        except Exception:
            return False

    if not isinstance(ch, (discord.TextChannel, discord.Thread)):
        return False

    try:
        await ch.send(
            content=f"<@{requester_id}> {text}",
            allowed_mentions=USER_PING_ALLOWED,
        )
        return True
    except Exception:
        return False


class MoveServerRequestModal(discord.ui.Modal, title="Move server request"):
    email = discord.ui.TextInput(
        label="Email address",
        placeholder="you@example.com",
        required=True,
        max_length=200,
    )
    reason = discord.ui.TextInput(
        label="Reason for the request",
        placeholder="Briefly explain why you need to move.",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Could not resolve your member info.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Cooldown check again at submit time (prevents opening multiple modals)
        remaining = _seconds_left(interaction.user.id)
        if remaining > 0:
            await interaction.followup.send(
                f"You're on cooldown for this request. Try again in **{remaining // 60}m {remaining % 60}s**.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        direction = _role_direction(interaction.user)
        if direction is None:
            await interaction.followup.send(
                "You can only use this if you have **exactly one** of these roles:\n"
                "- SS-VOD East\n"
                "- SS-VOD West",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        from_name, _, to_name, _ = direction

        staff_ch = await _fetch_requests_channel(guild)
        if staff_ch is None:
            await interaction.followup.send(
                "I can’t find the staff requests channel. Tell staff to check the channel ID config.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        rid = _new_request_id()
        now = _now()

        # Where was /move_server run?
        origin_channel = interaction.channel
        origin_channel_id = origin_channel.id if origin_channel else None

        embed = discord.Embed(
            title="Move server request",
            description=f"Requester: {interaction.user.mention} ({interaction.user})",
        )
        embed.add_field(name="Move", value=f"{from_name} → {to_name}", inline=False)
        embed.add_field(name="Email", value=str(self.email.value).strip(), inline=False)
        embed.add_field(name="Reason", value=str(self.reason.value).strip(), inline=False)
        if origin_channel_id:
            embed.add_field(name="Origin channel", value=f"<#{origin_channel_id}> ({origin_channel_id})", inline=False)
        embed.add_field(name="Requested", value=rel_ts(now), inline=True)
        embed.set_footer(text=f"Request ID: {rid} | Requester: {interaction.user.id}")

        ping_content = _staff_ping_content()
        try:
            await staff_ch.send(
                content=ping_content or None,
                embed=embed,
                view=MoveServerActionView(),
                allowed_mentions=STAFF_PINGS_ALLOWED if ping_content else NO_PINGS,
            )
            _mark_submitted(interaction.user.id)
        except Exception:
            await interaction.followup.send(
                "Something went wrong sending your request to staff. Try again in a moment.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        await interaction.followup.send(
            "Got it — your request has been sent to staff for review.",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class AcceptMoveModal(discord.ui.Modal, title="Accept move request"):
    plex_invite_url = discord.ui.TextInput(
        label="Plex invite URL",
        placeholder="https://...",
        required=True,
        max_length=500,
    )

    def __init__(self, request: dict):
        super().__init__()
        self.request = request

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to do that.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        requester_id = self.request["requester_id"]
        origin_channel_id = self.request.get("origin_channel_id")

        requester = guild.get_member(requester_id)
        if requester is None:
            try:
                requester = await guild.fetch_member(requester_id)
            except Exception:
                requester = None

        dm_ok = False
        dm_error = None
        dm_text = (
            "Your move request has been **approved**.\n\n"
            f"Plex invite link:\n{self.plex_invite_url.value.strip()}\n\n"
            "If you run into issues, reply in your ticket / support chat."
        )

        if requester:
            try:
                await requester.send(dm_text, allowed_mentions=NO_PINGS)
                dm_ok = True
            except Exception as e:
                dm_error = type(e).__name__
        else:
            dm_error = "MemberNotFound"

        # If DM failed, ping them in the channel they started in (no Plex URL)
        origin_pinged = False
        if not dm_ok:
            origin_pinged = await _notify_origin_channel_if_needed(
                guild=guild,
                origin_channel_id=origin_channel_id,
                requester_id=requester_id,
                text=(
                    "your move request was **approved**, but I couldn’t DM you the details. "
                    f"Please open a support ticket in <#{TICKET_CHANNEL_ID}> so staff can follow up."
                ),
            )

        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(title="Move server request")
        embed.add_field(name="Status", value="✅ Accepted", inline=True)
        embed.add_field(name="Handled by", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="DM sent", value=str(dm_ok), inline=True)
        if not dm_ok and dm_error:
            embed.add_field(name="DM error", value=dm_error, inline=True)
        if not dm_ok:
            embed.add_field(name="Origin ping sent", value=str(origin_pinged), inline=True)

        await interaction.message.edit(embed=embed, view=MoveServerActionView(disabled=True), allowed_mentions=NO_PINGS)
        await interaction.followup.send("Accepted.", ephemeral=True, allowed_mentions=NO_PINGS)

        audit = discord.Embed(
            title="Move request accepted",
            description=(
                f"Request ID: {self.request.get('request_id')}\n"
                f"Requester: <@{requester_id}> ({requester_id})\n"
                f"Move: {self.request.get('move')}\n"
                f"Handled by: {interaction.user} ({interaction.user.id})\n"
                f"DM sent: {dm_ok}\n"
                f"Origin ping sent: {origin_pinged if not dm_ok else 'n/a'}"
            ),
        )
        await send_audit_embed(guild, audit)


class DenyMoveModal(discord.ui.Modal, title="Deny move request"):
    deny_reason = discord.ui.TextInput(
        label="Reason for denial (staff only)",
        placeholder="Explain why this request was denied.",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, request: dict):
        super().__init__()
        self.request = request

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to do that.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        requester_id = self.request["requester_id"]
        origin_channel_id = self.request.get("origin_channel_id")

        requester = guild.get_member(requester_id)
        if requester is None:
            try:
                requester = await guild.fetch_member(requester_id)
            except Exception:
                requester = None

        dm_ok = False
        dm_error = None
        dm_text = (
            "Your move request has been **denied**.\n\n"
            "If you’d like to discuss it further, please reply in your ticket / support chat."
        )

        if requester:
            try:
                await requester.send(dm_text, allowed_mentions=NO_PINGS)
                dm_ok = True
            except Exception as e:
                dm_error = type(e).__name__
        else:
            dm_error = "MemberNotFound"

        # If DM failed, ping them in origin channel (NO denial reason)
        origin_pinged = False
        if not dm_ok:
            origin_pinged = await _notify_origin_channel_if_needed(
                guild=guild,
                origin_channel_id=origin_channel_id,
                requester_id=requester_id,
                text=(
                    "your move request was **denied**, but I couldn’t DM you. "
                    f"If you want to discuss further, please open a support ticket in <#{TICKET_CHANNEL_ID}>."
                ),
            )

        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(title="Move server request")
        embed.add_field(name="Status", value="❌ Denied", inline=True)
        embed.add_field(name="Handled by", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Denial reason (staff)", value=self.deny_reason.value.strip()[:1024], inline=False)
        embed.add_field(name="DM sent", value=str(dm_ok), inline=True)
        if not dm_ok and dm_error:
            embed.add_field(name="DM error", value=dm_error, inline=True)
        if not dm_ok:
            embed.add_field(name="Origin ping sent", value=str(origin_pinged), inline=True)

        await interaction.message.edit(embed=embed, view=MoveServerActionView(disabled=True), allowed_mentions=NO_PINGS)
        await interaction.followup.send("Denied.", ephemeral=True, allowed_mentions=NO_PINGS)

        audit = discord.Embed(
            title="Move request denied",
            description=(
                f"Request ID: {self.request.get('request_id')}\n"
                f"Requester: <@{requester_id}> ({requester_id})\n"
                f"Move: {self.request.get('move')}\n"
                f"Handled by: {interaction.user} ({interaction.user.id})\n"
                f"DM sent: {dm_ok}\n"
                f"Origin ping sent: {origin_pinged if not dm_ok else 'n/a'}"
            ),
        )
        await send_audit_embed(guild, audit)


class MoveServerActionView(discord.ui.View):
    """
    Persistent view for staff actions. It reads the request data from the embed.
    """
    def __init__(self, disabled: bool = False):
        super().__init__(timeout=None)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = disabled

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="move_server_accept",
    )
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to do that.", ephemeral=True)
            return

        if not interaction.message.embeds:
            await interaction.response.send_message("Missing request embed on this message.", ephemeral=True)
            return

        req = _parse_request_from_embed(interaction.message.embeds[0])
        if not req:
            await interaction.response.send_message("Couldn’t parse request details from the embed.", ephemeral=True)
            return

        await interaction.response.send_modal(AcceptMoveModal(req))

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        custom_id="move_server_deny",
    )
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to do that.", ephemeral=True)
            return

        if not interaction.message.embeds:
            await interaction.response.send_message("Missing request embed on this message.", ephemeral=True)
            return

        req = _parse_request_from_embed(interaction.message.embeds[0])
        if not req:
            await interaction.response.send_message("Couldn’t parse request details from the embed.", ephemeral=True)
            return

        await interaction.response.send_modal(DenyMoveModal(req))


def setup(bot):
    @bot.tree.command(
        name="move_server",
        description="Request a move between East/West (requires SS-VOD East or SS-VOD West).",
    )
    async def move_server(interaction: discord.Interaction):
        remaining = _seconds_left(interaction.user.id)
        if remaining > 0:
            await interaction.response.send_message(
                f"You're on cooldown for this request. Try again in **{remaining // 60}m {remaining % 60}s**.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        await interaction.response.send_modal(MoveServerRequestModal())
