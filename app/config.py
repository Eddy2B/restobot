# app/config.py — Environment variables and pure constants
# No dependencies on any other app module. Safe to import anywhere.

import os
import secrets

# ==============================================================
# ENVIRONMENT VARIABLES
# ==============================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
DATABASE_URL = os.getenv("DATABASE_URL", "")
PORT = int(os.getenv("PORT", 8000))
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", secrets.token_urlsafe(16))
APP_DOMAIN = os.getenv("APP_DOMAIN", "app.guestscale.com")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.getenv("BREVO_LIST_ID", "6"))

# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_FOUNDER = os.getenv("STRIPE_PRICE_FOUNDER", "")
STRIPE_PRICE_STANDARD = os.getenv("STRIPE_PRICE_STANDARD", "")

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# Legacy (migration only)
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "")

# ==============================================================
# PURE CONSTANTS (no env dependency)
# ==============================================================

TONE_PROMPTS = {
    "premium": "STYLE : Vouvoiement obligatoire. Langage soutenu et élégant. Formulations raffinées. Pas d'emojis. Ton d'un maître d'hôtel de palace.",
    "casual": "STYLE : Ton chaleureux et décontracté. Tutoiement accepté si le client tutoie. Emojis modérés (1-2 par message max).",
    "beach": "STYLE : Très décontracté et amical. Tutoiement naturel. Emojis fréquents. Ton léger et ensoleillé.",
    "classic": "STYLE : Vouvoiement systématique. Sobre et professionnel. Pas d'emojis. Phrases courtes et précises.",
}

PLAN_LIMITS = {"founder": 500, "standard": 500, "trial": 500}
PLAN_RATES = {"founder": 0.08, "standard": 0.06, "trial": 0.0}
WHATSAPP_BROADCAST_COST_CENTS = 15  # 0,15 € HT par message WhatsApp campagne
WALLET_TOPUP_AMOUNTS_CENTS = (500, 1000, 2500, 5000)  # 5 €, 10 €, 25 €, 50 €

RATE_LIMITS = {
    "/api/login": (5, 300),
    "/api/register": (3, 300),
    "/api/forgot-password": (3, 300),
    "/api/reset-password": (5, 300),
    "default": (60, 60),
}

MIDI_SLOTS = ["12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45","14:00","14:15"]
SOIR_SLOTS = ["19:00","19:15","19:30","19:45","20:00","20:15","20:30","20:45","21:00","21:15","21:30","21:45","22:00","22:15","22:30"]
ALL_SLOTS = MIDI_SLOTS + SOIR_SLOTS
MEAL_DURATION_SLOTS = 8  # 8 × 15min = 2h meal duration
