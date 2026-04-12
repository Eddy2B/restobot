# app/state.py — Centralized mutable state (singleton module)
from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limiter singleton — registered with app in main.py, importable by route modules
limiter = Limiter(key_func=get_remote_address)
#
# All shared mutable dicts and the db_pool live here. Import this module
# (not individual names) for mutable singletons that get reassigned:
#
#   import app.state as state
#   state.db_pool = await asyncpg.create_pool(...)   # reassignment propagates
#
# For dicts (mutated in place, never reassigned), either form works:
#   from app.state import restaurants_cache           # OK — same dict object
#   import app.state as state; state.restaurants_cache  # also OK

# ==============================================================
# DATABASE
# ==============================================================
db_pool = None  # asyncpg.Pool — set during startup, None until then

# ==============================================================
# RESTAURANT DATA (loaded from DB at startup, refreshed every 30s)
# ==============================================================
restaurants_cache = {}      # restaurant_id (UUID str): {id, slug, name, owner_phone, ...settings, trial_ends_at}
pid_to_restaurant = {}      # whatsapp_phone_number_id → restaurant_id (webhook routing)
phone_to_restaurant = {}    # normalized phone → restaurant_id (Twilio missed call routing)

# ==============================================================
# PER-RESTAURANT RUNTIME DATA
# ==============================================================
conversations = {}          # "restaurant_id:phone" → [messages]
bookings = {}               # restaurant_id → [bookings]
floor_tables = {}           # restaurant_id → [{id, seats, zone, x, y, w, h, shape}]
table_slots = {}            # restaurant_id → {"12:30": {"T1": "available"}}
table_statuses = {}         # rid → {"date:service:table_id": status}
table_groups = {}           # rid → [{"tables": ["T3","T4"], "name": "T3+T4"}]
review_queue = {}           # restaurant_id → [reviews]
contacts = {}               # restaurant_id → {phone: contact_data}
campaigns_store = {}        # rid → [campaign dicts]
restaurant_status = {}      # restaurant_id → {status, message, closed_dates, full_dates, ...}
stats = {}                  # restaurant_id → {messages_today, bookings_today, languages, last_reset}
daily_stats_history = {}    # restaurant_id → [snapshots]
waitlist = {}               # restaurant_id → [entries]
data_versions = {}          # restaurant_id → int
usage_counters = {}         # rid → {"2026-04": {"total": 0, ...}}

# ==============================================================
# AI / CONVERSATION STATE
# ==============================================================
ai_paused_conversations = {}  # rid → {phone: pause_until_iso}
escalations = {}              # rid → [escalation dicts]
missed_call_tracker = {}      # rid → {phone: {wa_sent_at, call_sent_at, date}}
expired_reply_tracker = {}    # rid → {phone: last_auto_reply_iso} (24h cooldown)

# ==============================================================
# SESSION / AUTH STATE
# ==============================================================
web_sessions = {}             # session_id → {rid, ...}
password_reset_tokens = {}    # code → {email, expires}
rate_limit_store = {}         # ip → {endpoint → [timestamps]}
login_failures = {}           # ip → {"count": int, "locked_until": float}
