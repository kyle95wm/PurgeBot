import datetime as dt
import secrets

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, send_audit_embed

ROLE_EAST_ID = 1466939252024541423  # SS-VOD East
ROLE_WEST_ID = 1466938881764233396  # SS-VOD West
ROLE_SOUTH_ID = 1472852339730681998  # SS-VOD South

MOVE_REQUESTS_CHANNEL_ID = 1468797510897373425
MOVE_SERVER_COOLDOWN_SECONDS = 60 * 60  # 1 hour

# user_id -> last used timestamp
MOVE_SERVER_LAST_USED: dict[int, dt.datetime] = {}

ROLE_NAMES = {
    ROLE_EAST_ID: "SS-VOD East",
    ROLE_WEST_ID: "SS-VOD West",
    ROLE_SOUTH_ID: "SS-VOD South",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _get_role_ids(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles if r != member.guild.default_role}


def _get_current_server_role(member: discord.Member) -> int | None:
    """
    Returns the role ID of the current server if the member has exactly one of East/West/South.
    Otherwise returns None.
    """
    role_ids = _get_role_ids(member)
    servers = [rid for rid in (ROLE_EAST_ID, ROLE_WEST_ID, ROLE_SOUTH_ID) if rid in role_ids]
    if len(servers) != 1:
        return None
    return servers[0]


def _allowed_destinations(current_role_id: int) -> list[int]:
    all_servers = [ROLE_EAST_ID, ROLE_WEST_ID, ROLE_SOUTH_ID]
    return [rid for rid in all_servers if rid != current_role_id]


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


def _parse_footer_ids(embed: discord.Embed) -> tuple[int, int, str]:
    """
    Footer format:
      Request ID: XXXX | Requester: 123 | SourceChannel: 456
    Returns (requester_id, source_channel_id, request_id)
    """
    footer = (embed.footer.text or "").strip()

    request_id = "UNKNOWN"
    requester_id = 0
    source_channel_id = 0

    parts = [p.strip() for p in footer.split("|")]
    for p in parts:
        if p.lower().startswith("request id:"):
            request_id = p.split(":", 1)[1].strip() if ":" in p else "UNKNOWN"
        elif p.lower().startswith("requester:"):
            val = p.split(":", 1)[1].strip()
            requester_id = int(val)
        elif p.lower().startswith("sourcechannel:"):
            val = p.split(":", 1)[1].strip()
            source_channel_id = int(val)

    if requester_id <= 0 or source_channel_id <= 0:
        raise ValueError("Couldn’t parse requester/source channel IDs from footer.")

    return requester_id, source_channel_id, request_id


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

        # Re-check their roles at submit time (in case roles changed)
        current_role = _get_current_server_role(interaction.user)
        if current_role is None:
            await interaction.followup.send(
                "You must have exactly one of:\n- SS-VOD East\n- SS-VOD West\n- SS-VOD South",
                ephemeral=True,
            )
            return

        allowed = _allowed_destinations(current_role)
        if self.to_role_id not in allowed:
            await interaction.followup.send(
                "That destination is not valid for your current server role. Please run `/move_server` again.",
                ephemeral=True,
            )
            return

        _mark_used(interaction.user.id)

        staff_ch = await _fetch_requests_channel(guild)
        if not staff_ch:
            await interaction.followup.send("Staff channel not found.", ephemeral=True)
            return

        rid = _new_request_id()

        from_name = ROLE_NAMES.get(current_role, str(current_role))
        to_name = ROLE_NAMES.get(self.to_role_id, str(self.to_role_id))

        embed = discord.Embed(
            title="Move server request",
            description=f"{interaction.user.mention} ({interaction.user})",
        )
        embed.add_field(name="Move", value=f"{from_name} → {to_name}", inline=False)
        embed.add_field(name="Email", value=self.email.value.strip(), inline=False)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
        embed.set_footer(
            text=f"Request ID: {rid} | Requester: {interaction.user.id} | SourceChannel: {self.source_channel_id}"
        )

        mention_staff = " ".join(f"<@{uid}>" for uid in ALLOWED_USER_IDS) if ALLOWED_USER_IDS else ""

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
                f"Request ID: `{rid}`\n"
                f"From: {from_name}\n"
                f"To: {to_name}\n"
                f"Staff msg: {msg.jump_url}"
            ),
        )
        await send_audit_embed(guild, audit)

        await interaction.followup.send("Your request has been sent to staff.", ephemeral=True)


class AcceptMoveModal(discord.ui.Modal, title="Accept move request"):
    plex_invite_url = discord.ui.TextInput(
        label="Plex invite URL",
        required=True,
        max_length=500,
    )

    def __init__(self, requester_id: int, source_channel_id: int, request_id: str):
        super().__init__()
        self.requester_id = requester_id
        self.source_channel_id = source_channel_id
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not interaction.message:
            return

        wrapped_url = f"<{self.plex_invite_url.value.strip()}>"

        requester = guild.get_member(self.requester_id)
        dm_ok = False
        if requester:
            try:
                await requester.send(
                    f"Your move request has been **approved**.\n\nPlex invite link:\n{wrapped_url}",
                    allowed_mentions=NO_PINGS,
                )
                dm_ok = True
            except Exception:
                dm_ok = False

        if not dm_ok:
            source_channel = guild.get_channel(self.source_channel_id)
            if isinstance(source_channel, discord.TextChannel):
                await source_channel.send(
                    f"<@{self.requester_id}> Your move request was approved. "
                    f"Please check your DMs. If you didn’t get one, open a support ticket.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(title="Move server request")
        embed.add_field(name="Status", value="✅ Accepted", inline=True)
        embed.add_field(name="Handled by", value=str(interaction.user), inline=False)
        embed.add_field(name="DM", value="✅ Sent" if dm_ok else "⚠️ Failed (fallback ping posted)", inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("Approved.", ephemeral=True)

        audit = discord.Embed(
            title="Move request accepted",
            description=(
                f"Request ID: `{self.request_id}`\n"
                f"Requester: <@{self.requester_id}>\n"
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

    def __init__(self, requester_id: int, source_channel_id: int, request_id: str):
        super().__init__()
        self.requester_id = requester_id
        self.source_channel_id = source_channel_id
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not interaction.message:
            return

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

        if not dm_ok:
            source_channel = guild.get_channel(self.source_channel_id)
            if isinstance(source_channel, discord.TextChannel):
                await source_channel.send(
                    f"<@{self.requester_id}> Your move request was denied. "
                    f"If you’d like to discuss it, please open a support ticket.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(title="Move server request")
        embed.add_field(name="Status", value="❌ Denied", inline=True)
        embed.add_field(name="Handled by", value=str(interaction.user), inline=False)
        embed.add_field(name="DM", value="✅ Sent" if dm_ok else "⚠️ Failed (fallback ping posted)", inline=False)
        embed.add_field(name="Deny reason (staff note)", value=self.deny_reason.value.strip()[:1024], inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("Denied.", ephemeral=True)

        audit = discord.Embed(
            title="Move request denied",
            description=(
                f"Request ID: `{self.request_id}`\n"
                f"Requester: <@{self.requester_id}>\n"
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
            requester_id, source_channel_id, request_id = _parse_footer_ids(embed)
        except Exception:
            await interaction.response.send_message("Couldn’t parse request metadata from embed footer.", ephemeral=True)
            return

        await interaction.response.send_modal(
            AcceptMoveModal(requester_id=requester_id, source_channel_id=source_channel_id, request_id=request_id)
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
            requester_id, source_channel_id, request_id = _parse_footer_ids(embed)
        except Exception:
            await interaction.response.send_message("Couldn’t parse request metadata from embed footer.", ephemeral=True)
            return

        await interaction.response.send_modal(
            DenyMoveModal(requester_id=requester_id, source_channel_id=source_channel_id, request_id=request_id)
        )


class DestinationSelect(discord.ui.Select):
    def __init__(self, *, from_role_id: int):
        self.from_role_id = from_role_id
        opts = []
        for rid in _allowed_destinations(from_role_id):
            opts.append(discord.SelectOption(label=ROLE_NAMES.get(rid, str(rid)), value=str(rid)))

        super().__init__(
            placeholder="Select the server you want to move to…",
            min_values=1,
            max_values=1,
            options=opts,
            custom_id="move_server:dest_select",
        )

    async def callback(self, interaction: discord.Interaction):
        # Once they pick a destination, open the modal
        if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        # Cooldown check early
        on_cd, remaining = _check_cooldown(interaction.user.id)
        if on_cd:
            mins = max(1, remaining // 60)
            await interaction.response.send_message(f"You can submit another request in {mins} minute(s).", ephemeral=True)
            return

        # Re-check their current role to ensure the options are still valid
        current_role = _get_current_server_role(interaction.user)
        if current_role is None:
            await interaction.response.send_message(
                "You must have exactly one of:\n- SS-VOD East\n- SS-VOD West\n- SS-VOD South",
                ephemeral=True,
            )
            return

        to_role_id = int(self.values[0])
        if to_role_id not in _allowed_destinations(current_role):
            await interaction.response.send_message(
                "That destination isn’t valid for your current role. Please run `/move_server` again.",
                ephemeral=True,
            )
            return

        source_channel_id = interaction.channel.id if interaction.channel else 0
        await interaction.response.send_modal(
            MoveServerRequestModal(
                source_channel_id=source_channel_id,
                from_role_id=current_role,
                to_role_id=to_role_id,
            )
        )


class DestinationSelectView(discord.ui.View):
    def __init__(self, *, from_role_id: int):
        super().__init__(timeout=300)  # 5 minutes
        self.add_item(DestinationSelect(from_role_id=from_role_id))


def setup(bot):
    @bot.tree.command(name="move_server", description="Request server move (East/West/South)")
    async def move_server(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        current_role = _get_current_server_role(interaction.user)
        if current_role is None:
            await interaction.response.send_message(
                "You must have exactly one of:\n- SS-VOD East\n- SS-VOD West\n- SS-VOD South",
                ephemeral=True,
            )
            return

        on_cd, remaining = _check_cooldown(interaction.user.id)
        if on_cd:
            mins = max(1, remaining // 60)
            await interaction.response.send_message(
                f"You can submit another request in {mins} minute(s).",
                ephemeral=True,
            )
            return

        from_name = ROLE_NAMES.get(current_role, str(current_role))
        view = DestinationSelectView(from_role_id=current_role)

        await interaction.response.send_message(
            f"Current server: **{from_name}**\nSelect where you want to move to:",
            ephemeral=True,
            view=view,
            allowed_mentions=NO_PINGS,
        )
