import os

# --------------------
# REQUIRED
# --------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN env var (set it in .env).")

XC_URL = os.getenv("XC_URL")
if not XC_URL:
    raise RuntimeError("Missing XC_URL env var (set it in .env).")

# --------------------
# SQLITE
# --------------------
# Persisted DB path (recommended to keep under /app/data with a docker volume)
SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/data/bot.sqlite3")

# --------------------
# OPTIONAL / CONFIG
# --------------------
AUDIT_LOG_CHANNEL_ID = int(os.getenv("AUDIT_LOG_CHANNEL_ID", "0")) or None

# Comma-separated list in .env, e.g. "123,456"
ALLOWED_USER_IDS = {
    int(x.strip())
    for x in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if x.strip()
}

# Ticket channel for /checkme messaging
TICKET_CHANNEL_ID = 1457896130653458542

# Role IDs
# Note: "Visitor" was renamed to "Member" in your server; ID stays the same.
VISITOR_ROLE_ID = 1457797392144273540
REDDITOR_ROLE_ID = 1463660506274336951
ALLOWED_ROLE_IDS = {VISITOR_ROLE_ID, REDDITOR_ROLE_ID}

# Purge safety defaults
DEFAULT_PURGE_DAYS = 7
CONFIRM_CODE_TTL_SECONDS = 15 * 60  # 15 minutes
KICK_DELAY_SECONDS = 1.2           # throttle to avoid rate limits
CONFIRM_PHRASE = "I UNDERSTAND"    # must match after normalization
GRACE_PERIOD_SECONDS = 60          # cancel window before kicks start

# /checkme cooldown
CHECKME_COOLDOWN_SECONDS = 10 * 60  # 10 minutes

# --------------------
# PURGE DM (env-only)
# --------------------
# In .env:
# PURGE_DM_ENABLED=true
# PURGE_DM_TEMPLATE=Hello {user},\n...\n{server}\n...{days}
#
# Supported placeholders:
#   {user}
#   {server}
#   {days}
#   {role_mode}
PURGE_DM_ENABLED = os.getenv("PURGE_DM_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

PURGE_DM_TEMPLATE = os.getenv("PURGE_DM_TEMPLATE", "").replace("\\n", "\n").strip()

# --------------------
# CENTRALIZED TEXT / TEMPLATES
# --------------------
GIVE_CREDS_HEADER = "Here are your credentials and expiry information:"
GIVE_CREDS_PASSWORD_NOTE = "Please remember this password is case sensitive!"

def format_creds_message(username: str, password: str, expiry: str) -> str:
    """
    Builds the full credential message (used for both in-channel and DM).
    Keep all copy in config so command files stay logic-only.
    """
    return (
        f"{GIVE_CREDS_HEADER}\n\n"
        f"XC URL: <{XC_URL}>\n"
        f"username: {username}\n"
        f"password: {password}\n\n"
        f"Expiration Date: {expiry}\n\n"
        f"{GIVE_CREDS_PASSWORD_NOTE}"
    )
