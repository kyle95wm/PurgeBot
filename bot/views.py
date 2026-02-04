import asyncio
import discord

from .config import TICKET_CHANNEL_ID
from .helpers import (
    NO_PINGS,
    checkme_on_cooldown,
    mark_checkme_used,
    build_checkme_message,
    PURGE_GRACE_PERIOD_SECONDS,
)

# --------------------
# Pagers
# --------------------
class SimplePagedView(discord.ui.View):
    def __init__(self, author_id: int, pages: list[str], title: str, description: str):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.pages = pages or ["(none)"]
        self.title = title
        self.description = description
        self.page_index = 0
        self._refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    def _refresh_buttons(self):
        self.prev_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= (len(self.pages) - 1)

    def build_embed(self) -> discord.Embed:
        page_total = max(1, len(self.pages))
        page_num = self.page_index + 1
        embed = discord.Embed(title=self.title, description=self.description)
        embed.add_field(name=f"Page {page_num}/{page_total}", value=self.pages[self.page_index], inline=False)
        embed.set_footer(text="Use buttons to page. Close disables controls.")
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page_index = max(0, self.page_index - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page_index = min(len(self.pages) - 1, self.page_index + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Closed.", embed=None, view=self, allowed_mentions=NO_PINGS)
        self.stop()


class GroupedRoleView(discord.ui.View):
    """Dropdown to switch between Member-only and Member+Redditor groups + Prev/Next paging."""

    def __init__(
        self,
        author_id: int,
        member_pages: list[str],
        redditor_pages: list[str],
        member_count: int,
        redditor_count: int,
    ):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.pages = {"member": member_pages or ["(none)"], "redditor": redditor_pages or ["(none)"]}
        self.counts = {"member": member_count, "redditor": redditor_count}
        self.group = "member"
        self.page_index = 0

        self.select.options = [
            discord.SelectOption(label=f"Member only ({member_count})", value="member"),
            discord.SelectOption(label=f"Member + Redditor ({redditor_count})", value="redditor"),
        ]
        self._refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    def _max_page_index(self) -> int:
        return max(0, len(self.pages[self.group]) - 1)

    def _refresh_buttons(self):
        self.prev_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= self._max_page_index()

    def build_embed(self) -> discord.Embed:
        total = self.counts["member"] + self.counts["redditor"]
        embed = discord.Embed(
            title="Members matching allowed roles",
            description=(
                f"Matched **{total}** member(s).\n"
                f"Criteria: **Member only** OR **Member + Redditor**, and **no other roles** (besides @everyone)."
            ),
        )
        group_name = "Member only" if self.group == "member" else "Member + Redditor"
        page_total = max(1, len(self.pages[self.group]))
        page_num = self.page_index + 1
        embed.add_field(
            name=f"{group_name} — page {page_num}/{page_total}",
            value=self.pages[self.group][self.page_index],
            inline=False,
        )
        embed.set_footer(text="Dropdown switches groups. Buttons page. Close disables controls.")
        return embed

    @discord.ui.select(placeholder="Choose a group…")
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.group = select.values[0]
        self.page_index = 0
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page_index = max(0, self.page_index - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.page_index = min(self._max_page_index(), self.page_index + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self, allowed_mentions=NO_PINGS)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Closed.", embed=None, view=self, allowed_mentions=NO_PINGS)
        self.stop()


# --------------------
# Grace cancel view
# --------------------
class GraceCancelView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=PURGE_GRACE_PERIOD_SECONDS + 15)
        self.author_id = author_id
        self.cancel_event = asyncio.Event()
        self.cancelled_by: int | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Cancel purge", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.cancelled_by = interaction.user.id
        self.cancel_event.set()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Purge cancelled.",
            embed=None,
            view=self,
            allowed_mentions=NO_PINGS,
        )
        self.stop()


# --------------------
# Check panel (persistent)
# --------------------
class CheckStatusPanelView(discord.ui.View):
    def __init__(self, guild_id: int | None = None):
        super().__init__(timeout=None)

        # Link button does not need a custom_id; Discord handles it client-side.
        if guild_id:
            url = f"https://discord.com/channels/{guild_id}/{TICKET_CHANNEL_ID}"
            self.add_item(
                discord.ui.Button(
                    label="Open a ticket",
                    style=discord.ButtonStyle.link,
                    url=url,
                )
            )

    @discord.ui.button(
        label="Check my status",
        style=discord.ButtonStyle.primary,
        custom_id="check_status_panel_v1",
    )
    async def check_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        on_cd, remaining = checkme_on_cooldown(interaction.user.id)
        if on_cd:
            minutes = (remaining + 59) // 60
            await interaction.response.send_message(
                f"You can use this again in **{minutes}** minute(s).",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return

        mark_checkme_used(interaction.user.id)

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = await guild.fetch_member(interaction.user.id)

        await interaction.response.send_message(
            build_checkme_message(member),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
