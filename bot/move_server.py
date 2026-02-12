import datetime as dt
import secrets

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, rel_ts, send_audit_embed


# Roles that are allowed to use /move_server
ROLE_EAST_ID = 1466939252024541423  # SS-VOD East
ROLE_WEST_ID = 1466938881764233396  # SS-VOD West

# Where staff actions land
MOVE_REQUESTS_CHANNEL_ID = 1468797510897373425


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


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


def _get_requests_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch = guild.get_channel(MOVE_REQUESTS_CHANNEL_ID)
    return ch if isinstance(ch, discord.TextChannel) else None


def _new_request_id() -> str:
    return secrets.token_hex(4).upper()  # 8 chars


def _find_field(embed: discord.Embed, name: str) -> str | None:
    for f in embed.fields:
        if (f.name or "").strip().lower() == name.strip().lower():
            return f.value
    return None


def _parse_request_from_embed(embed: discord.Embed) -> dict | None:
    """
    Pull request data from the embed we posted to staff.
    """
    rid = None
    requester_id = None

    # We store these in footer: "Request ID: XXXXXXXX | Requester: 123"
    if embed.footer and embed.footer.text:
        txt = embed.footer.text
        try:
            # very forgiving parsing
            parts = [p.strip() for p in txt.split("|")]
            for p in parts:
                if p.lower().startswith("request id:"):
                    rid = p.split(":", 1)[1].strip()
                if p.lower().startswith("requester:"):
                    requester_id = int(p.split(":", 1)[1].strip())
        except Exception:
            pass

    email = _find_field(embed, "Email")
    reason = _find_field(embed, "Reason")
    from_to = _find_field(embed, "Move")
    requested_at = _find_field(embed, "Requested")

    if not requester_id or not from_to:
        return None

    return {
        "request_id": rid or "UNKNOWN",
        "requester_id": requester_id,
        "email": email or "(unknown)",
        "reason": reason or "(none)",
        "move": from_to,
        "requested_at": requested_at or "(unknown)",
    }


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

    def __init__(self, *, from_name: str, from_role_id: int, to_name: str, to_role_id: int):
        super().__init__()
        self.from_name = from_name
        self.from_role_id = from_role_id
        self.to_name = to_name
        self.to_role_id = to_role_id

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        staff_ch = _get_requests_channel(guild)
        if staff_ch is None:
            await interaction.response.send_message(
                "I can’t find the staff requests channel. Tell staff to check the channel ID config.",
                ephemeral=True,
            )
            return

        rid = _new_request_id()
        now = _now()

        embed = discord.Embed(
            title="Move server request",
            description=f"Requester: {interaction.user.mention} ({interaction.user})",
        )
        embed.add_field(
            name="Move",
            value=f"{self.from_name} → {self.to_name}",
            inline=False,
        )
        embed.add_field(name="Email", value=str(self.email.value).strip(), inline=False)
        embed.add_field(name="Reason", value=str(self.reason.value).strip(), inline=False)
        embed.add_field(name="Requested", value=rel_ts(now), inline=True)

        embed.set_footer(text=f"Request ID: {rid} | Requester: {interaction.user.id}")

        view = MoveServerActionView()

        await staff_ch.send(embed=embed, view=view, allowed_mentions=NO_PINGS)

        await interaction.response.send_message(
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

        # staff-only
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to do that.", ephemeral=True)
            return

        requester_id = self.request["requester_id"]
        requester = guild.get_member(requester_id) or await guild.fetch_member(requester_id)

        dm_ok = False
        dm_error = None
        dm_text = (
            "Your move request has been **approved**.\n\n"
            f"Plex invite link:\n{self.plex_invite_url.value.strip()}\n\n"
            "If you run into issues, reply in your ticket / support chat."
        )
        try:
            await requester.send(dm_text, allowed_mentions=NO_PINGS)
            dm_ok = True
        except Exception as e:
            dm_error = f"{type(e).__name__}"

        # Update the staff message
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed = embed.copy()
            embed.add_field(name="Status", value="✅ Accepted", inline=True)
            embed.add_field(name="Handled by", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="DM sent", value=str(dm_ok), inline=True)
            if not dm_ok and dm_error:
                embed.add_field(name="DM error", value=dm_error, inline=True)

        # Disable buttons
        view = MoveServerActionView(disabled=True)

        await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)

        # Optional audit
        audit = discord.Embed(
            title="Move request accepted",
            description=(
                f"Request ID: {self.request.get('request_id')}\n"
                f"Requester: <@{requester_id}> ({requester_id})\n"
                f"Move: {self.request.get('move')}\n"
                f"Handled by: {interaction.user} ({interaction.user.id})\n"
                f"DM sent: {dm_ok}"
            ),
        )
        await send_audit_embed(guild, audit)


class DenyMoveModal(discord.ui.Modal, title="Deny move request"):
    deny_reason = discord.ui.TextInput(
        label="Reason for denial",
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

        # staff-only
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to do that.", ephemeral=True)
            return

        requester_id = self.request["requester_id"]
        requester = guild.get_member(requester_id) or await guild.fetch_member(requester_id)

        dm_ok = False
        dm_error = None
        dm_text = (
            "Your move request has been **denied**.\n\n"
            f"Reason:\n{self.deny_reason.value.strip()}\n\n"
            "If you think this is a mistake, reply in your ticket / support chat."
        )
        try:
            await requester.send(dm_text, allowed_mentions=NO_PINGS)
            dm_ok = True
        except Exception as e:
            dm_error = f"{type(e).__name__}"

        # Update the staff message
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed = embed.copy()
            embed.add_field(name="Status", value="❌ Denied", inline=True)
            embed.add_field(name="Handled by", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="Denial reason", value=self.deny_reason.value.strip()[:1024], inline=False)
            embed.add_field(name="DM sent", value=str(dm_ok), inline=True)
            if not dm_ok and dm_error:
                embed.add_field(name="DM error", value=dm_error, inline=True)

        view = MoveServerActionView(disabled=True)

        await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)

        audit = discord.Embed(
            title="Move request denied",
            description=(
                f"Request ID: {self.request.get('request_id')}\n"
                f"Requester: <@{requester_id}> ({requester_id})\n"
                f"Move: {self.request.get('move')}\n"
                f"Handled by: {interaction.user} ({interaction.user.id})\n"
                f"DM sent: {dm_ok}"
            ),
        )
        await send_audit_embed(guild, audit)


class MoveServerActionView(discord.ui.View):
    """
    Persistent view for staff actions. It reads the request data from the embed.
    """
    def __init__(self, disabled: bool = False):
        super().__init__(timeout=None)
        self._disabled = disabled
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = disabled

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="move_server_accept",
        disabled=False,
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
        disabled=False,
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
        description="Request a move between East/West (users must have SS-VOD East or SS-VOD West).",
    )
    async def move_server(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Could not resolve your member info.", ephemeral=True)
            return

        direction = _role_direction(interaction.user)
        if direction is None:
            await interaction.response.send_message(
                "You can only use this if you have **exactly one** of these roles:\n"
                "- SS-VOD East\n"
                "- SS-VOD West",
                ephemeral=True,
            )
            return

        from_name, from_role_id, to_name, to_role_id = direction
        await interaction.response.send_modal(
            MoveServerRequestModal(
                from_name=from_name,
                from_role_id=from_role_id,
                to_name=to_name,
                to_role_id=to_role_id,
            )
        )
