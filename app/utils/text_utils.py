# app/utils/text_utils.py — Pure text helpers (no shared state)

import re as re_mod
from html import escape as html_escape


def sanitize_input(value, max_length: int = 500):
    """Escape dangerous HTML characters and limit length."""
    if not isinstance(value, str):
        return value
    value = value.strip()
    value = html_escape(value, quote=True)
    return value[:max_length]


def sanitize_dict(data: dict, fields: list, max_length: int = 500):
    """Sanitize multiple string fields in a dict in-place."""
    for f in fields:
        if f in data and isinstance(data[f], str):
            data[f] = sanitize_input(data[f], max_length)
    return data


def normalize_phone(phone: str) -> str:
    """Normalize a phone number to international format (e.g. 33612345678)."""
    p = re_mod.sub(r'[^\d+]', '', phone.strip())
    if p.startswith('+'):
        p = p[1:]
    if p.startswith('00'):
        p = p[2:]
    if p.startswith('0') and len(p) == 10:
        p = '33' + p[1:]
    return p
