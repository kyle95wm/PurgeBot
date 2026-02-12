import datetime as dt

import discord
from discord.ext import commands

from .config import TOKEN, ALLOWED_USER_IDS, VISITOR_ROLE_ID
from .views import CheckStatusPanelView
from .helpers import send_audit_embed
from .db import ensure_db
from .invite_tracking import snapshot_invites_to_db, detect_used_invite, log_join_event

# Import command modules directly (don’t rely on commands/__init__.py exporting anything)
from .commands import checkme, check, check_panel, list_roles, purge, bot_info, give_creds, test_purge_dm
from .commands import invite as invite_cmd


intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Bump this whenever you want a visible version change in /bot_info
bot.version = "modular-v1"


@bot.event
async def on_ready():
    # uptime tracking
    if not hasattr(bot, "started_at") or bot.started_at is None:
        bot.started_at = dt.datetime.now(dt.timezone.utc)

    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Allowed user IDs: {sorted(ALLOWED_USER_IDS)}")

    # Persistent panel handler (survives restarts)
    bot.add_view(CheckStatusPanelView())

    # DB init
    await ensure_db()

    # Snapshot invites for every guild we're in
    for g in bot.guilds:
        try:
            await snapshot_invites_to_db(g)
        except discord.Forbidden:
            print(f"[invite-tracking] Missing permissions to read invites in guild {g.id}")
        except Exception as e:
            print(f"[invite-tracking] Snapshot failed in guild {g.id}: {type(e).__name__}: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print("Command sync failed:", e)


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    # --------------------
    # AUTO-ASSIGN MEMBER ROLE ON JOIN
    # --------------------
    try:
        role = guild.get_role(VISITOR_ROLE_ID)
        if role is None:
            print(f"[auto-role] Role {VISITOR_ROLE_ID} not found in guild {guild.id}")
        else:
            await member.add_roles(role, reason="Auto-assign Member role on join")
    except discord.Forbidden:
        print(f"[auto-role] Missing permissions / role hierarchy to assign {VISITOR_ROLE_ID} in guild {guild.id}")
    except Exception as e:
        print(f"[auto-role] Failed to assign role: {type(e).__name__}: {e}")

    # --------------------
    # INVITE TRACKING + LOGGING
    # --------------------
    invite_info = None
    unknown_reason = None

    try:
        invite_info = await detect_used_invite(guild)
        if invite_info is None:
            # No error, but no delta detected (common with vanity / expired / 1-use race timing)
            unknown_reason = "unknown (no invite delta detected — vanity/expired/race)"
    except discord.Forbidden:
        # This is the big one: can create invites, but can’t READ invites without Manage Server perms
        unknown_reason = "unknown (missing permission to read invites — give bot Manage Server)"
    except Exception as e:
        unknown_reason = f"unknown (invite check error: {type(e).__name__})"

    try:
        await log_join_event(guild_id=guild.id, member=member, invite_info=invite_info)
    except Exception as e:
        print(f"[invite-tracking] Failed to log join: {type(e).__name__}: {e}")

    embed = discord.Embed(
        title="Member joined",
        description=f"{member} ({member.id}) joined.",
    )

    if invite_info:
        inviter = f"<@{invite_info['inviter_id']}>" if invite_info.get("inviter_id") else "unknown"
        embed.add_field(name="Invite", value=f"`{invite_info['code']}`", inline=True)
        embed.add_field(name="Inviter", value=inviter, inline=True)
        embed.add_field(name="Uses", value=f"{invite_info['before']} → {invite_info['after']}", inline=True)
    else:
        embed.add_field(name="Invite", value=unknown_reason or "unknown", inline=False)

    await send_audit_embed(guild, embed)


def load_commands():
    checkme.setup(bot)
    check.setup(bot)
    check_panel.setup(bot)
    list_roles.setup(bot)
    purge.setup(bot)
    bot_info.setup(bot)
    give_creds.setup(bot)
    test_purge_dm.setup(bot)
    invite_cmd.setup(bot)


load_commands()
bot.run(TOKEN)
