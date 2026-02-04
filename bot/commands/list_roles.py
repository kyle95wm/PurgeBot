import discord
from discord import app_commands

from ..config import ALLOWED_USER_IDS, REDDITOR_ROLE_ID
from ..helpers import (
    NO_PINGS,
    RoleMode,
    member_matches_role_mode,
    role_ids_excluding_everyone,
    newest_first,
    chunk_lines,
    line_for_member,
    pretty_role_mode,
)
from ..views import SimplePagedView, GroupedRoleView

def setup(bot):
    @bot.tree.command(
        name="list_only_allowed_roles",
        description="List members with only Member, or Member + Redditor roles (no other roles).",
    )
    @app_commands.describe(
        include_bots="Include bot accounts in results (default: false).",
        role_mode="Filter: both (default), redditor_only, or member_only.",
    )
    async def list_only_allowed_roles(
        interaction: discord.Interaction,
        include_bots: bool = False,
        role_mode: RoleMode = "both",
    ):
        if interaction.user.id not in ALLOWED_USER_IDS:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server, not DMs.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if role_mode == "both":
            member_only: list[discord.Member] = []
            member_plus_redditor: list[discord.Member] = []

            async for m in guild.fetch_members(limit=None):
                if not include_bots and m.bot:
                    continue
                if not member_matches_role_mode(m, "both"):
                    continue

                ids = role_ids_excluding_everyone(m)
                if REDDITOR_ROLE_ID in ids:
                    member_plus_redditor.append(m)
                else:
                    member_only.append(m)

            member_only.sort(key=newest_first, reverse=True)
            member_plus_redditor.sort(key=newest_first, reverse=True)

            member_pages = chunk_lines([line_for_member(m) for m in member_only] or ["(none)"])
            redditor_pages = chunk_lines([line_for_member(m) for m in member_plus_redditor] or ["(none)"])

            view = GroupedRoleView(
                author_id=interaction.user.id,
                member_pages=member_pages,
                redditor_pages=redditor_pages,
                member_count=len(member_only),
                redditor_count=len(member_plus_redditor),
            )
            await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True, allowed_mentions=NO_PINGS)
            return

        filtered: list[discord.Member] = []
        async for m in guild.fetch_members(limit=None):
            if not include_bots and m.bot:
                continue
            if not member_matches_role_mode(m, role_mode):
                continue
            filtered.append(m)

        filtered.sort(key=newest_first, reverse=True)

        pages = chunk_lines([line_for_member(m) for m in filtered] or ["(none)"])
        title = "Members matching allowed roles"
        desc = f"Filter: **{pretty_role_mode(role_mode)}** (no other roles besides @everyone). Matched **{len(filtered)}** member(s)."

        view = SimplePagedView(author_id=interaction.user.id, pages=pages, title=title, description=desc)
        await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True, allowed_mentions=NO_PINGS)
