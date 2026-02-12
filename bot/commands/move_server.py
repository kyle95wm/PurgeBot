import datetime as dt
import secrets

import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, rel_ts, send_audit_embed


ROLE_EAST_ID = 1466939252024541423
ROLE_WEST_ID = 1466938881764233396

MOVE_REQUESTS_CHANNEL_ID = 1468797510897373425
MOVE_SERVER_COOLDOWN_SECONDS = 60 * 60  # 1 hour

MOVE_SERVER_LAST_USED: dict[int, dt.datetime] = {}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _get_role_ids(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles if r != member.guild.default_role}


def _role_direction(member: discord.Member):
    role_ids = _get_role_ids(member)
    has_east = ROLE_EAST_ID in role_ids
    has_west = ROLE_WEST_ID in role_ids

    if has_east and not has_west:
        return ("SS-VOD East", ROLE_EAST_ID, "SS-VOD West", ROLE_WEST_ID)
    if has_west and not has_east:
        return ("SS-VOD West", ROLE_WEST_ID, "SS-VOD East", ROLE_EAST_ID)

    return None


async def _fetch_requests_channel(guild: discord.Guild):
    ch = guild.get_channel(MOVE_REQUESTS_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        fetched = await guild.fetch_channel(MOVE_REQUESTS_CHANNEL_ID)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


def _new_request_id():
    return secrets.token_hex(4).upper()


def _check_cooldown(user_id: int):
    now = _now()
    last = MOVE_SERVER_LAST_USED.get(user_id)
    if not last:
        return False, 0

    elapsed = (now - last).total_seconds()
    if elapsed >= MOVE_SERVER_COOLDOWN_SECONDS:
        return False, 0

    return True, int(MOVE_SERVER_COOLDOWN_SECONDS - elapsed)


def _mark_used(user_id: int):
    MOVE_SERVER_LAST_USED[user_id] = _now()


class MoveServerRequestModal(discord.ui.Modal, title="Move server request"):
    email = discord.ui.TextInput(label="Email address", required=True)
    reason = discord.ui.TextInput(
        label="Reason for the request",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

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
            await interaction.followup.send(
                f"You can submit another request in {remaining // 60} minutes.",
                ephemeral=True,
            )
            return

        direction = _role_direction(interaction.user)
        if direction is None:
            await interaction.followup.send(
                "You must have exactly one of:\n"
                "- SS-VOD East\n"
                "- SS-VOD West",
                ephemeral=True,
            )
            return

        _mark_used(interaction.user.id)

        from_name, _, to_name, _ = direction
        staff_ch = await _fetch_requests_channel(guild)
        if not staff_ch:
            await interaction.followup.send("Staff channel not found.", ephemeral=True)
            return

        rid = _new_request_id()

        embed = discord.Embed(
            title="Move server request",
            description=f"{interaction.user.mention} ({interaction.user})",
        )
        embed.add_field(name="Move", value=f"{from_name} → {to_name}", inline=False)
        embed.add_field(name="Email", value=self.email.value.strip(), inline=False)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
        embed.set_footer(text=f"Request ID: {rid} | Requester: {interaction.user.id}")

        mention_staff = " ".join(f"<@{uid}>" for uid in ALLOWED_USER_IDS)

        await staff_ch.send(
            content=f"{mention_staff}",
            embed=embed,
            view=MoveServerActionView(),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        await interaction.followup.send(
            "Your request has been sent to staff.",
            ephemeral=True,
        )


class AcceptMoveModal(discord.ui.Modal, title="Accept move request"):
    plex_invite_url = discord.ui.TextInput(
        label="Plex invite URL",
        required=True,
        max_length=500,
    )

    def __init__(self, request, source_channel_id, requester_id):
        super().__init__()
        self.request = request
        self.source_channel_id = source_channel_id
        self.requester_id = requester_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            return

        requester = guild.get_member(self.requester_id)
        wrapped_url = f"<{self.plex_invite_url.value.strip()}>"

        dm_ok = False
        if requester:
            try:
                await requester.send(
                    f"Your move request has been **approved**.\n\n"
                    f"Plex invite link:\n{wrapped_url}",
                    allowed_mentions=NO_PINGS,
                )
                dm_ok = True
            except Exception:
                dm_ok = False

        # Fallback ping if DM fails
        if not dm_ok:
            source_channel = guild.get_channel(self.source_channel_id)
            if isinstance(source_channel, discord.TextChannel):
                await source_channel.send(
                    f"<@{self.requester_id}> Your move request was approved. "
                    f"Please check your DMs. If you did not receive one, open a support ticket.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        embed = interaction.message.embeds[0].copy()
        embed.add_field(name="Status", value="✅ Accepted", inline=True)
        embed.add_field(name="Handled by", value=str(interaction.user), inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("Approved.", ephemeral=True)


class DenyMoveModal(discord.ui.Modal, title="Deny move request"):
    deny_reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=True,
    )

    def __init__(self, source_channel_id, requester_id):
        super().__init__()
        self.source_channel_id = source_channel_id
        self.requester_id = requester_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            return

        requester = guild.get_member(self.requester_id)

        dm_ok = False
        if requester:
            try:
                await requester.send(
                    "Your move request has been **denied**.\n\n"
                    "If you would like to discuss this further, please open a support ticket.",
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
                    f"If you would like to discuss further, please open a support ticket.",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        embed = interaction.message.embeds[0].copy()
        embed.add_field(name="Status", value="❌ Denied", inline=True)
        embed.add_field(name="Handled by", value=str(interaction.user), inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send("Denied.", ephemeral=True)


class MoveServerActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        footer = embed.footer.text
        requester_id = int(footer.split("Requester: ")[1])

        await interaction.response.send_modal(
            AcceptMoveModal(embed, interaction.channel.id, requester_id)
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        footer = embed.footer.text
        requester_id = int(footer.split("Requester: ")[1])

        await interaction.response.send_modal(
            DenyMoveModal(interaction.channel.id, requester_id)
        )


def setup(bot):
    @bot.tree.command(name="move_server", description="Request server move (East/West)")
    async def move_server(interaction: discord.Interaction):
        await interaction.response.send_modal(MoveServerRequestModal())
