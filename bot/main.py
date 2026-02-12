import datetime as dt
import discord
from discord.ext import commands

from .config import TOKEN, ALLOWED_USER_IDS
from .views import CheckStatusPanelView
from .helpers import send_audit_embed, NO_PINGS
from .db import ensure_db
from .invite_tracking import snapshot_invites_to_db, detect_used_invite, log_join_event

from .commands import checkme, check, check_panel, list_roles, purge, bot_info, give_creds, test_purge_dm


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

    invite_info = None
    try:
        invite_info = await detect_used_invite(guild)
    except discord.Forbidden:
        # Can't fetch invites
        invite_info = None
    except Exception:
        invite_info = None

    try:
        await log_join_event(guild_id=guild.id, member=member, invite_info=invite_info)
    except Exception as e:
        print(f"[invite-tracking] Failed to log join: {type(e).__name__}: {e}")

    # Audit log embed (simple)
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
        embed.add_field(name="Invite", value="unknown (vanity/expired/race/permissions)", inline=False)

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


load_commands()
bot.run(TOKEN)
