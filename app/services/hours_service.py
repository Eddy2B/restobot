# app/services/hours_service.py
"""Helpers pour la gestion des horaires structurés des restaurants."""

from datetime import datetime
from typing import Optional
from app.utils.date_utils import now_paris, MOIS_FR

# Mapping jour de la semaine Python (Monday=0) -> clé hours_structured
WEEKDAY_KEYS = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}

# Labels FR pour affichage
WEEKDAY_LABELS_FR = {
    "monday": "Lundi",
    "tuesday": "Mardi",
    "wednesday": "Mercredi",
    "thursday": "Jeudi",
    "friday": "Vendredi",
    "saturday": "Samedi",
    "sunday": "Dimanche",
}

ORDERED_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def get_hours_structured(settings: dict) -> Optional[dict]:
    """
    Retourne hours_structured s'il existe et est valide, sinon None.
    Le caller peut fallback sur settings.get("hours", "") (texte libre).
    """
    hs = settings.get("hours_structured")
    if not hs or not isinstance(hs, dict):
        return None
    # Valider qu'au moins un jour est défini
    if not any(day in hs for day in ORDERED_DAYS):
        return None
    return hs


def get_day_config(settings: dict, day_key: str) -> Optional[dict]:
    """
    Retourne la config d'un jour donné, ex:
    {"open": True, "services": [{"name": "Déjeuner", "start": "12:00", "end": "14:30"}]}
    """
    hs = get_hours_structured(settings)
    if not hs:
        return None
    return hs.get(day_key)


def is_open_today(settings: dict, now: Optional[datetime] = None) -> Optional[bool]:
    """
    True si ouvert aujourd'hui, False si fermé, None si hours_structured absent.
    """
    hs = get_hours_structured(settings)
    if not hs:
        return None
    if now is None:
        now = now_paris()
    day_key = WEEKDAY_KEYS[now.weekday()]
    day_conf = hs.get(day_key)
    if not day_conf:
        return None
    return bool(day_conf.get("open", False))


def get_today_services(settings: dict, now: Optional[datetime] = None) -> list:
    """
    Retourne la liste des services aujourd'hui (ou [] si fermé).
    Chaque service : {"name": str, "start": "HH:MM", "end": "HH:MM"}
    """
    hs = get_hours_structured(settings)
    if not hs:
        return []
    if now is None:
        now = now_paris()
    day_key = WEEKDAY_KEYS[now.weekday()]
    day_conf = hs.get(day_key)
    if not day_conf or not day_conf.get("open"):
        return []
    return day_conf.get("services", []) or []


def format_hours_for_prompt(settings: dict, now: Optional[datetime] = None) -> str:
    """
    Formate les horaires pour le system prompt envoyé à Claude.
    Format structuré, lisible, avec emphase sur le jour actuel.
    """
    hs = get_hours_structured(settings)
    if not hs:
        # Fallback sur le champ texte libre
        return settings.get("hours", "") or "Horaires non renseignés"

    if now is None:
        now = now_paris()
    today_key = WEEKDAY_KEYS[now.weekday()]

    lines = ["HORAIRES D'OUVERTURE (à respecter strictement) :"]
    for day in ORDERED_DAYS:
        conf = hs.get(day, {})
        label = WEEKDAY_LABELS_FR[day]
        if not conf.get("open"):
            lines.append(f"  • {label} : FERMÉ")
            continue
        services = conf.get("services", []) or []
        if not services:
            lines.append(f"  • {label} : Ouvert (horaires non renseignés)")
            continue
        svc_strs = [f"{s['name']} {s['start']}-{s['end']}" for s in services]
        lines.append(f"  • {label} : {', '.join(svc_strs)}")

    # Bloc AUJOURD'HUI en emphase
    lines.append("")
    today_conf = hs.get(today_key, {})
    today_label = WEEKDAY_LABELS_FR[today_key]
    today_date = f"{now.day} {MOIS_FR[now.month - 1]} {now.year}"
    if not today_conf.get("open"):
        lines.append(f"AUJOURD'HUI ({today_label} {today_date}) : FERMÉ — Ne propose AUCUNE réservation pour aujourd'hui. Propose un autre jour.")
    else:
        services = today_conf.get("services", []) or []
        if not services:
            lines.append(f"AUJOURD'HUI ({today_label} {today_date}) : Ouvert mais horaires non renseignés.")
        else:
            svc_strs = [f"{s['name']} {s['start']}-{s['end']}" for s in services]
            lines.append(f"AUJOURD'HUI ({today_label} {today_date}) : {', '.join(svc_strs)}")
            # Calcul simple des horaires exploitables
            all_ends = [s['end'] for s in services]
            last_end = max(all_ends)
            lines.append(f"ATTENTION : Dernier service se termine à {last_end}. Ne propose pas d'horaires après.")

    return "\n".join(lines)


def validate_hours_structured(hs: dict) -> tuple:
    """
    Valide la structure avant sauvegarde. Retourne (True, "") ou (False, erreur).
    """
    if not isinstance(hs, dict):
        return False, "hours_structured doit être un objet"

    for day in hs:
        if day not in ORDERED_DAYS:
            return False, f"Jour inconnu : {day}"
        conf = hs[day]
        if not isinstance(conf, dict):
            return False, f"Config invalide pour {day}"
        if "open" not in conf:
            return False, f"Clé 'open' manquante pour {day}"
        if conf["open"]:
            services = conf.get("services", [])
            if not isinstance(services, list):
                return False, f"services doit être une liste pour {day}"
            for i, s in enumerate(services):
                if not isinstance(s, dict):
                    return False, f"service {i} invalide pour {day}"
                for key in ("name", "start", "end"):
                    if key not in s:
                        return False, f"service {i} de {day} : clé '{key}' manquante"
                # Valider format HH:MM
                for field in ("start", "end"):
                    val = s[field]
                    if not isinstance(val, str) or len(val) != 5 or val[2] != ":":
                        return False, f"service {i} de {day} : format {field} invalide (attendu HH:MM)"
                    try:
                        h = int(val[:2])
                        m = int(val[3:])
                        if not (0 <= h <= 23 and 0 <= m <= 59):
                            raise ValueError
                    except ValueError:
                        return False, f"service {i} de {day} : format {field} invalide"
                # Vérifier start < end
                if s["start"] >= s["end"]:
                    return False, f"service {i} de {day} : start doit être < end"
    return True, ""
