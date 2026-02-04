import os

# ---- Roles ----
VISITOR_ROLE_ID = 1457797392144273540  # "Member"
REDDITOR_ROLE_ID = 1463660506274336951
ALLOWED_ROLE_IDS = {VISITOR_ROLE_ID, REDDITOR_ROLE_ID}

# ---- Channels ----
TICKET_CHANNEL_ID = 1457896130653458542

# ---- Env parsing ----
def parse_id_set(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out = set()
    for p in raw.split(","):
        p = p.strip()
        if p:
            out.add(int(p))
    return out

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN")

AUDIT_LOG_CHANNEL_ID = int(os.getenv("AUDIT_LOG_CHANNEL_ID", "0")) or None

ALLOWED_USER_IDS = parse_id_set(os.getenv("ALLOWED_USER_IDS"))
if not ALLOWED_USER_IDS:
    raise RuntimeError("ALLOWED_USER_IDS missing or empty")

# ---- Defaults ----
DEFAULT_PURGE_DAYS = 7
CHECKME_COOLDOWN_SECONDS = 15 * 60
CONFIRM_CODE_TTL_SECONDS = 15 * 60
KICK_DELAY_SECONDS = 1.2
CONFIRM_PHRASE = "I UNDERSTAND"
GRACE_PERIOD_SECONDS = 60
