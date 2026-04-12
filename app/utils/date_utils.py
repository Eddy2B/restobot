# app/utils/date_utils.py — Pure date helpers (no shared state)

from datetime import datetime, date

MOIS_FR = [
    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
    'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
]

JOURS_FR = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']


def today_paris() -> date:
    """Get today's date in Europe/Paris timezone."""
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Europe/Paris")).date()
    except Exception:
        return date.today()


def now_paris() -> datetime:
    """Get current datetime in Europe/Paris timezone."""
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.utcnow()


def format_date_fr(d) -> str:
    """Format a date in French: 'mercredi 1 avril 2026'."""
    if isinstance(d, datetime):
        d = d.date()
    return f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month - 1]} {d.year}"


def _last_day_of_current_month_iso() -> str:
    """Returns ISO date string of last day of current month in Paris TZ."""
    import calendar
    today = today_paris()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return f"{today.year:04d}-{today.month:02d}-{last_day:02d}"
