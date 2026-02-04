import datetime as dt
import discord
from discord.ext import commands

from .config import TOKEN, ALLOWED_USER_IDS
from .views import CheckStatusPanelView

from .commands import checkme, check, check_panel, list_roles, purge
from .commands import bot_info  # NEW

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Simple version string you can bump whenever you want
bot.version = "modular-v1"

@bot.event
async def on_ready():
    # track uptime
    if not hasattr(bot, "started_at") or bot.started_at is None:
        bot.started_at = dt.datetime.now(dt.timezone.utc)

    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Allowed user IDs: {sorted(ALLOWED_USER_IDS)}")

    # Persistent panel button handler (works after restarts)
    bot.add_view(CheckStatusPanelView())

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print("Command sync failed:", e)

def load_commands():
    checkme.setup(bot)
    check.setup(bot)
    check_panel.setup(bot)
    list_roles.setup(bot)
    purge.setup(bot)
    bot_info.setup(bot)  # NEW

load_commands()
bot.run(TOKEN)
