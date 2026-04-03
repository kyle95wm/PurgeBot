import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS
from ..helpers import NO_PINGS, send_audit_embed


ANNOUNCEMENT_COLOR = 0x4D8D97

ANNOUNCEMENT_CHANNELS: dict[str, tuple[int, str]] = {
    "visitor": (1470191304422981632, "Visitor Announcements"),
    "member": (1457906370123796652, "Member Announcements"),
    "iptv": (1460022184520057004, "IPTV Announcements"),
}

PING_OPTIONS: dict[str, tuple[str, tuple[int, ...]]] = {
    "none": ("No role ping", ()),
    "ss_vod": ("Ping SS VOD", (1457562969817878702,)),
    "ss_tv": ("Ping SS TV", (1457567060031967264,)),
    "both": ("Ping SS VOD + SS TV", (1457562969817878702, 1457567060031967264)),
}


def _channel_choices() -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=channel_name, value=channel_key)
        for channel_key, (_, channel_name) in ANNOUNCEMENT_CHANNELS.items()
    ]


def _ping_choices() -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=label, value=ping_key)
        for ping_key, (label, _) in PING_OPTIONS.items()
    ]


def _build_announcement_embed(body: str) -> discord.Embed:
    return discord.Embed(description=body, color=ANNOUNCEMENT_COLOR)


def _role_mentions(role_ids: tuple[int, ...]) -> str:
    return " ".join(f"<@&{role_id}>" for role_id in role_ids)


def _build_message_payload(*, body: str, role_ids: tuple[int, ...], as_embed: bool) -> tuple[str | None, discord.Embed | None]:
    mention_text = _role_mentions(role_ids)
    clean_body = body.strip()

    if as_embed:
        return (mention_text or None), _build_announcement_embed(clean_body)

    if mention_text:
        return f"{mention_text}\n\n{clean_body}", None

    return clean_body, None


async def _resolve_announcement_channel(guild: discord.Guild, channel_key: str) -> discord.TextChannel | None:
    channel_id, _ = ANNOUNCEMENT_CHANNELS[channel_key]

    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel

    try:
        fetched = await guild.fetch_channel(channel_id)
    except Exception:
        return None

    return fetched if isinstance(fetched, discord.TextChannel) else None


class AnnouncementPreviewView(discord.ui.View):
    def __init__(self, *, author_id: int, channel_key: str, ping_key: str, as_embed: bool, body: str):
        super().__init__(timeout=900)
        self.author_id = author_id
        self.channel_key = channel_key
        self.ping_key = ping_key
        self.as_embed = as_embed
        self.body = body.strip()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This preview isn’t for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Post", style=discord.ButtonStyle.success)
    async def post_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        channel = await _resolve_announcement_channel(guild, self.channel_key)
        if channel is None:
            await interaction.response.send_message("Announcement channel not found.", ephemeral=True)
            return

        _, role_ids = PING_OPTIONS[self.ping_key]
        content, embed = _build_message_payload(body=self.body, role_ids=role_ids, as_embed=self.as_embed)

        try:
            posted = await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=bool(role_ids)),
            )
        except discord.Forbidden:
            await interaction.response.send_message("I can’t post in that channel (missing perms).", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Failed to post the announcement (Discord API error).", ephemeral=True)
            return

        audit = discord.Embed(
            title="Announcement posted",
            description=(
                f"Staff: {interaction.user} ({interaction.user.id})\n"
                f"Channel: {channel.mention}\n"
                f"Ping option: {PING_OPTIONS[self.ping_key][0]}\n"
                f"Embed mode: {'yes' if self.as_embed else 'no'}\n"
                f"Message: {posted.jump_url}"
            ),
            color=ANNOUNCEMENT_COLOR,
        )
        await send_audit_embed(guild, audit)

        await interaction.response.edit_message(
            content=f"Announcement posted in {channel.mention}: {posted.jump_url}",
            embed=None,
            view=None,
            attachments=[],
        )

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def edit_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(
            AnnouncementModal(
                channel_key=self.channel_key,
                ping_key=self.ping_key,
                as_embed=self.as_embed,
                initial_body=self.body,
            )
        )

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.secondary)
    async def discard_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(content="Announcement discarded.", embed=None, view=None, attachments=[])


class AnnouncementModal(discord.ui.Modal, title="Create announcement"):
    announcement = discord.ui.TextInput(
        label="Announcement",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=2000,
    )

    def __init__(self, *, channel_key: str, ping_key: str, as_embed: bool, initial_body: str = ""):
        super().__init__()
        self.channel_key = channel_key
        self.ping_key = ping_key
        self.as_embed = as_embed
        self.announcement.default = initial_body

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        ping_label, role_ids = PING_OPTIONS[self.ping_key]
        body = str(self.announcement).strip()
        content, embed = _build_message_payload(body=body, role_ids=role_ids, as_embed=self.as_embed)

        preview_view = AnnouncementPreviewView(
            author_id=interaction.user.id,
            channel_key=self.channel_key,
            ping_key=self.ping_key,
            as_embed=self.as_embed,
            body=body,
        )

        preview_header = (
            "Preview only you can see.\n"
            f"Channel: <#{ANNOUNCEMENT_CHANNELS[self.channel_key][0]}>\n"
            f"Ping: {ping_label}\n"
            f"Embed: {'yes' if self.as_embed else 'no'}"
        )

        await interaction.response.send_message(
            content=preview_header if content is None else f"{preview_header}\n\n{content}",
            embed=embed,
            view=preview_view,
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


def setup(bot):
    @bot.tree.command(name="announce", description="Staff-only: create and preview an announcement before posting.")
    @app_commands.describe(
        destination="Which announcement channel to post in.",
        ping="Optional role ping to include.",
        as_embed="Post the announcement body as an embed.",
    )
    @app_commands.choices(destination=_channel_choices(), ping=_ping_choices())
    async def announce(
        interaction: discord.Interaction,
        destination: str,
        ping: str = "none",
        as_embed: bool = False,
    ):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        channel = await _resolve_announcement_channel(guild, destination)
        if channel is None:
            await interaction.response.send_message("Announcement channel not found.", ephemeral=True)
            return

        await interaction.response.send_modal(
            AnnouncementModal(
                channel_key=destination,
                ping_key=ping,
                as_embed=as_embed,
            )
        )