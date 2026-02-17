import datetime as dt
import secrets

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, send_audit_embed


# ============================================================
# CONFIG (single source of truth)
# Add future servers by adding ONE line here.
# ============================================================
SERVER_ROLES: dict[int, str] = {
    1466939252024541423: "SS-VOD East",
    1466938881764233396: "SS-VOD West",
    1472852339730681998: "SS-VOD South",
}

MOVE_REQUESTS_CHANNEL_ID = 1468797510897373425
MOVE_SERVER_COOLDOWN_SECONDS = 60 * 60  # 1 hour

# Where to ping the requester if DM fails (always this channel)
MOVE_FALLBACK_PING_CHANNEL_ID = 1458533908701380719

# user_id -> last used timestamp (in-memory)
MOVE_SERVER_LAST_USED: dict[int, dt.datetime] = {}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _new_request_id() -> str:
    return secrets.token_hex(4).upper()


def _check_cooldown(user_id: int) -> tuple[bool, int]:
    now = _now()
    last = MOVE_SERVER_LAST_USED.get(user_id)
    if not last:
        return False, 0

    elapsed = (now - last).total_seconds()
    if elapsed >= MOVE_SERVER_COOLDOWN_SECONDS:
        return False, 0

    return True, int(MOVE_SERVER_COOLDOWN_SECONDS - elapsed)


def _mark_used(user_id: int) -> None:
    MOVE_SERVER_LAST_USED[user_id] = _now()


def _get_role_ids(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles if r != member.guild.default_role}


def _get_current_server_role(member: discord.Member) -> int | None:
    """Return the single server role ID the member has, or None if 0 or multiple."""
    role_ids = _get_role_ids(member)
    owned = [rid for rid in SERVER_ROLES.keys() if rid in role_ids]
    if len(owned) != 1:
        return None
    return owned[0]


def _allowed_destinations(current_role_id: int) -> list[int]:
    """All server role IDs except the current one."""
    return [rid for rid in SERVER_ROLES.keys() if rid != current_role_id]


async def _fetch_requests_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch = guild.get_channel(MOVE_REQUESTS_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        fetched = await guild.fetch_channel(MOVE_REQUESTS_CHANNEL_ID)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


async def _fetch_fallback_ping_channel(guild: discord.Guild) -> discord.TextChannel | None:
    ch = guild.get_channel(MOVE_FALLBACK_PING_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        fetched = await guild.fetch_channel(MOVE_FALLBACK_PING_CHANNEL_ID)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


def _parse_footer_ids(embed: discord.Embed) -> tuple[int, int, str, int, int]:
    """
    Footer format:
      Request ID: XXXX | Requester: 123 | SourceChannel: 456 | FromRole: 111 | ToRole: 222
    Returns: (requester_id, source_channel_id, request_id, from_role_id, to_role_id)
    """
    footer = (embed.footer.text or "").strip()

    request_id = "UNKNOWN"
    requester_id = 0
    source_channel_id = 0
    from_role_id = 0
    to_role_id = 0

    parts = [p.strip() for p in footer.split("|")]
    for p in parts:
        low = p.lower()
        if low.startswith("request id:"):
            request_id = p.split(":", 1)[1].strip() if ":" in p else "UNKNOWN"
        elif low.startswith("requester:"):
            requester_id = int(p.split(":", 1)[1].strip())
        elif low.startswith("sourcechannel:"):
            source_channel_id = int(p.split(":", 1)[1].strip())
        elif low.startswith("fromrole:"):
            from_role_id = int(p.split(":", 1)[1].strip())
        elif low.startswith("torole:"):
            to_role_id = int(p.split(":", 1)[1].strip())

    if requester_id <= 0 or source_channel_id <= 0 or from_role_id <= 0 or to_role_id <= 0:
        raise ValueError("Couldn’t parse required IDs from footer.")

    return requester_id, source_channel_id, request_id, from_role_id, to_role_id


# ============================================================
# USER FLOW:
# 1) /move_server -> ephemeral dropdown to choose destination
# 2) "Continue" -> modal for email/reason
# ============================================================

class MoveServerRequestModal(discord.ui.Modal, title="Move server request"):
    email = discord.ui.TextInput(label="Email address", required=True)
    reason = discord.ui.TextInput(
        label="Reason for the request",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, *, source_channel_id: int, from_role_id: int, to_role_id: int):
        super().__init__()
        self.source_channel_id = source_channel_id
        self.from_role_id = from_role_id
        self.to_role_id = to_role_id

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Could not resolve member info.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        on_cd, remaining = _check_cooldown(interaction.user.id)
        if on_cd:
            mins = max(1, remaining // 60)
            await interaction.followup.send(f"You can submit another request in {mins} minute(s).", ephemeral=True)
            return

        current = _get_current_server_role(interaction.user)
        if current is None:
            allowed = "\n".join(f"- {name}" for name in SERVER_ROLES.values())
            await interaction.followup.send(
                "You must have exactly one server role to use this:\n" + allowed,
                ephemeral=True,
            )
            return

        if current != self.from_role_id:
            await interaction.followup.send(
                "Your server role changed while you were filling this out. Please run `/move_server` again.",
                ephemeral=True,
            )
            return

        if self.to_role_id not in _allowed_destinations(current):
            await interaction.followup.send(
                "That destination is not valid for your current server role. Please run `/move_server` again.",
                ephemeral=True,
            )
            return

        staff_ch = await _fetch_requests_channel(guild)
        if not staff_ch:
            await interaction.followup.send("Staff channel not found.", ephemeral=True)
            return

        _mark_used(interaction.user.id)

        rid = _new_request_id()
        from_name = SERVER_ROLES.get(self.from_role_id, str(self.from_role_id))
        to_name = SERVER_ROLES.get(self.to_role_id, str(self.to_role_id))

        embed = discord.Embed(
            title="Move server request",
            description=f"{interaction.user.mention} ({interaction.user})",
        )
        embed.add_field(name="Move", value=f"{from_name} → {to_name}", inline=False)
        embed.add_field(name="Email", value=self.email.value.strip(), inline=False)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
        embed.set_footer(
            text=(
                f"Request ID: {rid} | Requester: {interaction.user.id} | SourceChannel: {self.source_channel_id} | "
                f"FromRole: {self.from_role_id} | ToRole: {self.to_role_id}"
            )
        )

        mention_staff = " ".join(f"<@{uid}>" for uid in sorted(ALLOWED_USER_IDS)) if ALLOWED_USER_IDS else ""

        msg = await staff_ch.send(
            content=mention_staff,
            embed=embed,
            view=MoveServerActionView(),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        audit = discord.Embed(
            title="Move request created",
            description=(
                f"Requester: {interaction.user} ({interaction.user.id})\n"
                f"Move: {from_name} → {to_name}\n"
                f"Request ID: `{rid}`\n"
                f"Staff msg: {msg.jump_url}"
            ),
        )
        await send_audit_embed(guild, audit)

        await interaction.followup.send("Your request has been sent to staff.", ephemeral=True)


class DestinationSelect(discord.ui.Select):
    def __init__(self, *, from_role_id: int, destination_role_ids: list[int]):
        options = [
            discord.SelectOption(label=SERVER_ROLES.get(rid, str(rid)), value=str(rid))
            for rid in destination_role_ids
        ]

        super().__init__(
            placeholder="Pick a destination server…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.from_role_id = from_role_id

    async def callback(self, interaction: discord.Interaction):
        view: "MoveServerDestinationView" = self.view  # type: ignore

        chosen = int(self.values[0])
        view.selected_to_role_id = chosen

        # Option B:
        # - disable dropdown after pick
        # - enable Continue
        self.disabled = True
        view.continue_button.disabled = False

        chosen_name = SERVER_ROLES.get(chosen, str(chosen))
        await interaction.response.edit_message(
            content=f"Destination saved: **{chosen_name}**\nClick **Continue** to submit your request.",
            view=view,
        )


class MoveServerDestinationView(discord.ui.View):
    def __init__(self, *, author_id: int, source_channel_id: int, from_role_id: int, destination_role_ids: list[int]):
        super().__init__(timeout=180)  # ephemeral, non-persistent
        self.author_id = author_id
        self.source_channel_id = source_channel_id
        self.from_role_id = from_role_id
        self.selected_to_role_id: int | None = None

        self.add_item(DestinationSelect(from_role_id=from_role_id, destination_role_ids=destination_role_ids))

        self.continue_button = discord.ui.Button(label="Continue", style=discord.ButtonStyle.primary, disabled=True)
        self.continue_button.callback = self._on_continue  # type: ignore
        self.add_item(self.continue_button)

        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel  # type: ignore
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn’t for you.", ephemeral=True)
            return False
        return True

    async def _on_cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Cancelled.", view=None)

    async def _on_continue(self, interaction: discord.Interaction):
        if self.selected_to_role_id is None:
            await interaction.response.send_message("Pick a destination first.", ephemeral=True)
            return

        await interaction.response.send_modal(
            MoveServerRequestModal(
                source_channel_id=self.source_channel_id,
                from_role_id=self.from_role_id,
                to_role_id=self.selected_to_role_id,
            )
        )


# ============================================================
# STAFF FLOW (persistent buttons)
# ============================================================

class AcceptMoveModal(discord.ui.Modal, title="Accept move request"):
    plex_invite_url = discord.ui.TextInput(
        label="Plex invite URL",
        required=True,
        max_length=500,
    )

    def __init__(self, *, requester_id: int, source_channel_id: int, request_id: str, from_role_id: int, to_role_id: int):
        super().__init__()
        self.requester_id = requester_id
        self.source_channel_id = source_channel_id  # kept for metadata; fallback ping channel is fixed
        self.request_id = request_id
        self.from_role_id = from_role_id
        self.to_role_id = to_role_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not interaction.message:
            return

        wrapped_url = f"<{self.plex_invite_url.value.strip()}>"
        from_name = SERVER_ROLES.get(self.from_role_id, str(self.from_role_id))
        to_name = SERVER_ROLES.get(self.to_role_id, str(self.to_role_id))

        requester = guild.get_member(self.requester_id)
        dm_ok = False
        if requester:
            try:
                await requester.send(
                    f"Your move request has been **approved**.\n\n"
                    f"Move: **{from_name} → {to_name}**\n\n"
                    f"Plex invite link:\n{wrapped_url}",
                    allowed_mentions=NO_PINGS,
                )
                dm_ok = True
            except Exception:
                dm_ok = False

        # Fallback ping in fixed channel if DM fails
        if not dm_ok:
            fb = await _fetch_fallback_ping_channel(guild)
            if fb:
                await fb.send(
                    f"<@{self.requester_id}> Your move request was approved. "
                    f"Please check your DMs. If you didn’t get one, open a support ticket.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(title="Move server request")
        embed.add_field(name="Status", value="✅ Accepted", inline=True)
        embed.add_field(name="Handled by", value=str(interaction.user), inline=False)
        embed.add_field(name="DM", value="✅ Sent" if dm_ok else f"⚠️ Failed (fallback ping in <#{MOVE_FALLBACK_PING_CHANNEL_ID}>)", inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("Approved.", ephemeral=True)

        audit = discord.Embed(
            title="Move request accepted",
            description=(
                f"Request ID: `{self.request_id}`\n"
                f"Requester: <@{self.requester_id}>\n"
                f"Move: {from_name} → {to_name}\n"
                f"Handler: {interaction.user} ({interaction.user.id})\n"
                f"DM: {'sent' if dm_ok else 'failed'}"
            ),
        )
        await send_audit_embed(guild, audit)


class DenyMoveModal(discord.ui.Modal, title="Deny move request"):
    deny_reason = discord.ui.TextInput(
        label="Reason (staff-only note)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, *, requester_id: int, source_channel_id: int, request_id: str, from_role_id: int, to_role_id: int):
        super().__init__()
        self.requester_id = requester_id
        self.source_channel_id = source_channel_id  # kept for metadata; fallback ping channel is fixed
        self.request_id = request_id
        self.from_role_id = from_role_id
        self.to_role_id = to_role_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not interaction.message:
            return

        from_name = SERVER_ROLES.get(self.from_role_id, str(self.from_role_id))
        to_name = SERVER_ROLES.get(self.to_role_id, str(self.to_role_id))

        requester = guild.get_member(self.requester_id)
        dm_ok = False
        if requester:
            try:
                await requester.send(
                    "Your move request has been **denied**.\n\n"
                    "If you’d like to discuss this further, please open a support ticket.",
                    allowed_mentions=NO_PINGS,
                )
                dm_ok = True
            except Exception:
                dm_ok = False

        # Fallback ping in fixed channel if DM fails (no reason posted)
        if not dm_ok:
            fb = await _fetch_fallback_ping_channel(guild)
            if fb:
                await fb.send(
                    f"<@{self.requester_id}> Your move request was denied. "
                    f"If you’d like to discuss it, please open a support ticket.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(title="Move server request")
        embed.add_field(name="Status", value="❌ Denied", inline=True)
        embed.add_field(name="Handled by", value=str(interaction.user), inline=False)
        embed.add_field(name="DM", value="✅ Sent" if dm_ok else f"⚠️ Failed (fallback ping in <#{MOVE_FALLBACK_PING_CHANNEL_ID}>)", inline=False)
        embed.add_field(name="Deny reason (staff note)", value=self.deny_reason.value.strip()[:1024], inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("Denied.", ephemeral=True)

        audit = discord.Embed(
            title="Move request denied",
            description=(
                f"Request ID: `{self.request_id}`\n"
                f"Requester: <@{self.requester_id}>\n"
                f"Move: {from_name} → {to_name}\n"
                f"Handler: {interaction.user} ({interaction.user.id})\n"
                f"DM: {'sent' if dm_ok else 'failed'}"
            ),
        )
        await send_audit_embed(guild, audit)


class MoveServerActionView(discord.ui.View):
    """
    Persistent view (registered in on_ready). Requirements:
      - timeout=None
      - every item has custom_id
    """
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="move_server:accept",
    )
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("Missing request embed on this message.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        try:
            requester_id, source_channel_id, request_id, from_role_id, to_role_id = _parse_footer_ids(embed)
        except Exception:
            await interaction.response.send_message("Couldn’t parse request metadata from embed footer.", ephemeral=True)
            return

        await interaction.response.send_modal(
            AcceptMoveModal(
                requester_id=requester_id,
                source_channel_id=source_channel_id,
                request_id=request_id,
                from_role_id=from_role_id,
                to_role_id=to_role_id,
            )
        )

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        custom_id="move_server:deny",
    )
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("Missing request embed on this message.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        try:
            requester_id, source_channel_id, request_id, from_role_id, to_role_id = _parse_footer_ids(embed)
        except Exception:
            await interaction.response.send_message("Couldn’t parse request metadata from embed footer.", ephemeral=True)
            return

        await interaction.response.send_modal(
            DenyMoveModal(
                requester_id=requester_id,
                source_channel_id=source_channel_id,
                request_id=request_id,
                from_role_id=from_role_id,
                to_role_id=to_role_id,
            )
        )


# ============================================================
# SLASH COMMAND
# ============================================================

def setup(bot):
    @bot.tree.command(name="move_server", description="Request server move (East/West/South)")
    async def move_server(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        on_cd, remaining = _check_cooldown(interaction.user.id)
        if on_cd:
            mins = max(1, remaining // 60)
            await interaction.response.send_message(f"You can submit another request in {mins} minute(s).", ephemeral=True)
            return

        current_role_id = _get_current_server_role(interaction.user)
        if current_role_id is None:
            allowed = "\n".join(f"- {name}" for name in SERVER_ROLES.values())
            await interaction.response.send_message(
                "You must have exactly one server role to use this:\n" + allowed,
                ephemeral=True,
            )
            return

        dest_ids = _allowed_destinations(current_role_id)
        if not dest_ids:
            await interaction.response.send_message("No destinations available.", ephemeral=True)
            return

        source_channel_id = interaction.channel.id if interaction.channel else 0
        from_name = SERVER_ROLES.get(current_role_id, str(current_role_id))

        view = MoveServerDestinationView(
            author_id=interaction.user.id,
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
