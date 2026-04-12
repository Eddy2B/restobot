# app/services/wallet_service.py — Wallet CRUD (balance, credit, debit, transactions)
# Dependencies: app.state (db_pool, restaurants_cache), app.services.db_helpers (db_save_restaurant, bump_version)

import logging

import app.state as _state
from app.services.db_helpers import db_save_restaurant, bump_version

logger = logging.getLogger("guestscale")


def get_wallet_cents(rid: str) -> int:
    rest = _state.restaurants_cache.get(rid, {})
    return int(rest.get("settings", {}).get("wallet_balance_cents", 0) or 0)


async def _log_wallet_txn(rid: str, txn_type: str, amount_cents: int, balance_after: int,
                          description: str = "", stripe_session_id: str = None, campaign_id: str = None):
    if not _state.db_pool:
        return
    try:
        async with _state.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO mt_wallet_transactions
                (restaurant_id, txn_type, amount_cents, balance_after_cents, description, stripe_session_id, campaign_id)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (stripe_session_id) DO NOTHING
            """, rid, txn_type, amount_cents, balance_after, description, stripe_session_id, campaign_id)
    except Exception as e:
        logger.error(f"Wallet txn log error: {e}")


async def credit_wallet(rid: str, amount_cents: int, description: str = "Recharge",
                        stripe_session_id: str = None) -> bool:
    """Crédite le wallet et journalise la transaction. Idempotent via stripe_session_id."""
    rest = _state.restaurants_cache.get(rid)
    if not rest or amount_cents <= 0:
        return False
    if stripe_session_id and _state.db_pool:
        try:
            async with _state.db_pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT 1 FROM mt_wallet_transactions WHERE stripe_session_id = $1",
                    stripe_session_id,
                )
                if existing:
                    logger.info(f"Wallet topup already processed for session {stripe_session_id}")
                    return False
        except Exception as e:
            logger.error(f"Wallet idempotency check error: {e}")
    settings = rest.setdefault("settings", {})
    current = int(settings.get("wallet_balance_cents", 0) or 0)
    new_balance = current + amount_cents
    settings["wallet_balance_cents"] = new_balance
    await db_save_restaurant(rid, rest)
    await _log_wallet_txn(rid, "topup", amount_cents, new_balance, description, stripe_session_id=stripe_session_id)
    bump_version(rid)
    return True


async def debit_wallet(rid: str, amount_cents: int, description: str = "",
                       campaign_id: str = None) -> bool:
    """Débite le wallet et journalise. Retourne False si solde insuffisant."""
    rest = _state.restaurants_cache.get(rid)
    if not rest or amount_cents <= 0:
        return False
    settings = rest.setdefault("settings", {})
    current = int(settings.get("wallet_balance_cents", 0) or 0)
    if current < amount_cents:
        return False
    new_balance = current - amount_cents
    settings["wallet_balance_cents"] = new_balance
    await db_save_restaurant(rid, rest)
    if description or campaign_id:
        await _log_wallet_txn(rid, "debit", -amount_cents, new_balance, description, campaign_id=campaign_id)
    return True


async def get_wallet_transactions(rid: str, limit: int = 10) -> list:
    if not _state.db_pool:
        return []
    try:
        async with _state.db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT txn_type, amount_cents, balance_after_cents, description, created_at
                FROM mt_wallet_transactions
                WHERE restaurant_id = $1::uuid
                ORDER BY created_at DESC
                LIMIT $2
            """, rid, limit)
            return [{
                "type": r["txn_type"],
                "amount_cents": r["amount_cents"],
                "amount_eur": round(r["amount_cents"] / 100, 2),
                "balance_after_cents": r["balance_after_cents"],
                "description": r["description"] or "",
                "date": r["created_at"].isoformat() if r["created_at"] else "",
            } for r in rows]
    except Exception as e:
        logger.error(f"Wallet txn fetch error: {e}")
        return []
