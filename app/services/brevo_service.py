# app/services/brevo_service.py — Brevo transactional email functions
# Dependencies: httpx, logging, app.config (BREVO_API_KEY, BREVO_LIST_ID)

import logging
from datetime import datetime

import httpx

from app.config import BREVO_API_KEY, BREVO_LIST_ID

logger = logging.getLogger("guestscale")


async def send_brevo_welcome(email: str, first_name: str, restaurant_name: str, password: str = ""):
    """Send welcome email via Brevo API and add contact to trial list."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping welcome email")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Create/update contact and add to list
            await client.post(
                "https://api.brevo.com/v3/contacts",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "email": email,
                    "attributes": {"PRENOM": first_name, "NOM_RESTAURANT": restaurant_name},
                    "listIds": [BREVO_LIST_ID],
                    "updateEnabled": True,
                }
            )
            logger.info(f"Brevo: contact {email} added to list {BREVO_LIST_ID}")

            # 2. Send transactional welcome email with credentials
            pwd_display = password if password else "(celui choisi lors de votre inscription)"
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": email, "name": first_name}],
                    "subject": f"Bienvenue sur GuestScale, {first_name} !",
                    "htmlContent": f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<div style="text-align:center;margin-bottom:24px">
<svg viewBox="0 0 32 32" fill="none" width="40" height="40" xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
<h1 style="font-size:24px;font-weight:800;color:#111827;margin:12px 0 4px">Bienvenue sur GuestScale !</h1>
<p style="font-size:14px;color:#6B7280">Votre essai gratuit de 30 jours est active.</p>
</div>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:20px;margin-bottom:20px">
<p style="font-size:14px;color:#374151;margin:0 0 8px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="font-size:14px;color:#374151;margin:0 0 8px"><strong>Votre identifiant :</strong> {email}</p>
<p style="font-size:14px;color:#374151;margin:0"><strong>Votre mot de passe :</strong> {pwd_display}</p>
</div>
<div style="background:#FEF3C7;border:1px solid #FCD34D;border-radius:12px;padding:14px;margin-bottom:20px">
<p style="font-size:12px;color:#92400E;margin:0">Nous vous recommandons de conserver cet email. Vous pouvez modifier votre mot de passe depuis votre tableau de bord.</p>
</div>
<div style="text-align:center;margin-bottom:20px">
<a href="https://app.guestscale.com/login" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#2D7DD2,#4ECDC4);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px">Acceder a mon dashboard</a>
</div>
<p style="font-size:13px;color:#6B7280;text-align:center">Des questions ? Repondez directement a cet email.</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Restaurant AI Platform<br>Nice, France</p>
</div>""",
                }
            )
            if resp.status_code < 300:
                logger.info(f"Brevo: welcome email sent to {email}")
            else:
                logger.error(f"Brevo email error: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Brevo error: {e}")


async def send_admin_notification_email(user_email: str, first_name: str, last_name: str, restaurant_name: str, phone: str):
    """Notify contact@guestscale.com when a new restaurant registers."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping admin notification")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": "contact@guestscale.com", "name": "GuestScale Admin"}],
                    "subject": f"Nouvelle inscription : {restaurant_name}",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:24px">
<h2 style="color:#111827;margin:0 0 16px">Nouvelle inscription GuestScale</h2>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:16px;margin-bottom:16px">
<p style="margin:4px 0;font-size:14px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Nom :</strong> {first_name} {last_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Email :</strong> {user_email}</p>
<p style="margin:4px 0;font-size:14px"><strong>Tel :</strong> {phone or 'Non renseigne'}</p>
<p style="margin:4px 0;font-size:14px"><strong>Date :</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
</div>
<p style="font-size:13px;color:#6B7280">Total restaurants : {len(restaurants_cache)}</p>
</div>""",
                }
            )
            if resp.status_code < 300:
                logger.info(f"Admin notification sent for {restaurant_name}")
            else:
                logger.error(f"Admin notif error: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Admin notification error: {e}")


async def send_subscription_welcome_emails(user_email: str, first_name: str, restaurant_name: str, plan: str):
    """Send subscription welcome to user + admin notification when a paid plan is activated."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping subscription welcome emails")
        return
    plan_label = "Fondateur" if plan == "founder" else "Standard"
    plan_price = "99 €" if plan == "founder" else "149 €"
    perks_html = (
        '<li style="margin:4px 0">500 messages IA inclus / mois</li>'
        '<li style="margin:4px 0">Agent WhatsApp 24/7</li>'
        '<li style="margin:4px 0">CRM, plan de salle, campagnes</li>'
    )
    if plan == "founder":
        perks_html += (
            '<li style="margin:4px 0"><strong>Support prioritaire WhatsApp</strong></li>'
            '<li style="margin:4px 0"><strong>Tarif bloqué à vie</strong></li>'
            '<li style="margin:4px 0"><strong>Configuration offerte (valeur 299 €)</strong></li>'
        )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Welcome to the user
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": user_email, "name": first_name or restaurant_name}],
                    "subject": f"Bienvenue dans GuestScale — Plan {plan_label} activé !",
                    "htmlContent": f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<div style="text-align:center;margin-bottom:24px">
<svg viewBox="0 0 32 32" fill="none" width="40" height="40"><circle cx="10" cy="10" r="4" fill="#2D7DD2"/><circle cx="22" cy="10" r="4" fill="#4ECDC4"/><circle cx="16" cy="22" r="4" fill="#4ECDC4"/><line x1="13" y1="11" x2="19" y2="11" stroke="#2D7DD2" stroke-width="2"/><line x1="11" y1="13" x2="15" y2="19" stroke="#2D7DD2" stroke-width="2"/><line x1="21" y1="13" x2="17" y2="19" stroke="#4ECDC4" stroke-width="2"/></svg>
<h1 style="font-size:24px;font-weight:800;color:#111827;margin:12px 0 4px">Bienvenue dans GuestScale !</h1>
<p style="font-size:14px;color:#6B7280">Votre Plan {plan_label} est désormais actif.</p>
</div>
<p style="font-size:14px;color:#374151;line-height:1.6">Bonjour {first_name or 'cher restaurateur'},</p>
<p style="font-size:14px;color:#374151;line-height:1.6">Merci de nous faire confiance pour <strong>{restaurant_name}</strong>. Voici ce qui est inclus dans votre Plan {plan_label} ({plan_price} HT/mois) :</p>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:18px;margin:18px 0">
<ul style="margin:0;padding-left:20px;font-size:14px;color:#374151;line-height:1.7">
{perks_html}
</ul>
</div>
<div style="text-align:center;margin:24px 0">
<a href="https://app.guestscale.com/dashboard" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#2D7DD2,#4ECDC4);color:#fff;font-size:15px;font-weight:700;text-decoration:none;border-radius:10px">Accéder à mon dashboard</a>
</div>
<p style="font-size:13px;color:#6B7280;line-height:1.6">Une question ? Une demande ? Répondez directement à cet email ou écrivez-nous à contact@guestscale.com — on vous répond rapidement.</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Restaurant AI Platform · Nice, France</p>
</div>""",
                }
            )
            # 2. Admin notification
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": "contact@guestscale.com", "name": "GuestScale Admin"}],
                    "subject": f"💰 Nouveau client payant : {restaurant_name} — Plan {plan_label}",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:24px">
<h2 style="color:#111827;margin:0 0 16px">Nouveau client payant</h2>
<div style="background:#E6FAF8;border:1px solid #4ECDC4;border-radius:12px;padding:16px;margin-bottom:16px">
<p style="margin:4px 0;font-size:14px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Email :</strong> {user_email}</p>
<p style="margin:4px 0;font-size:14px"><strong>Plan :</strong> {plan_label} ({plan_price} HT/mois)</p>
<p style="margin:4px 0;font-size:13px;color:#6B7280"><strong>Date :</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
</div>
</div>""",
                }
            )
            logger.info(f"Subscription welcome emails sent for {restaurant_name}")
    except Exception as e:
        logger.error(f"Subscription welcome email error: {e}")


async def send_cancellation_emails(user_email: str, first_name: str, restaurant_name: str,
                                   effective_date: str, reason: str = ""):
    """Send cancellation confirmation to user + admin notification to GuestScale."""
    if not BREVO_API_KEY:
        logger.warning("No BREVO_API_KEY — skipping cancellation emails")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Confirmation to the user
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": user_email, "name": first_name}],
                    "subject": "Confirmation de résiliation — GuestScale",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">
<h1 style="font-size:22px;font-weight:800;color:#111827;margin:0 0 16px">Résiliation confirmée</h1>
<p style="font-size:14px;color:#374151;line-height:1.6">Bonjour {first_name},</p>
<p style="font-size:14px;color:#374151;line-height:1.6">Nous avons bien enregistré la résiliation de votre abonnement GuestScale pour <strong>{restaurant_name}</strong>.</p>
<div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:12px;padding:18px;margin:18px 0">
<p style="margin:0 0 6px;font-size:14px"><strong>Fin d'abonnement :</strong> {effective_date}</p>
<p style="margin:0;font-size:13px;color:#6B7280">Votre dashboard reste accessible jusqu'à cette date. Aucun frais supplémentaire ne sera prélevé.</p>
</div>
<p style="font-size:13px;color:#374151;line-height:1.6">Vos données restent exportables pendant 30 jours après cette date depuis l'onglet « Mon compte » → « Données personnelles ».</p>
<p style="font-size:13px;color:#374151;line-height:1.6">Vous pouvez annuler la résiliation à tout moment avant le {effective_date} depuis votre dashboard.</p>
<p style="font-size:13px;color:#6B7280;line-height:1.6;margin-top:20px">Merci pour votre confiance,<br>L'équipe GuestScale</p>
<hr style="border:none;border-top:1px solid #E5E7EB;margin:20px 0">
<p style="font-size:11px;color:#9CA3AF;text-align:center">GuestScale — Nice, France · contact@guestscale.com</p>
</div>""",
                }
            )
            # 2. Admin notification
            reason_html = f"<p style='margin:4px 0;font-size:14px'><strong>Motif :</strong> {reason}</p>" if reason else "<p style='margin:4px 0;font-size:13px;color:#6B7280'>Aucun motif fourni.</p>"
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"name": "GuestScale", "email": "contact@guestscale.com"},
                    "to": [{"email": "contact@guestscale.com", "name": "GuestScale Admin"}],
                    "subject": f"⚠️ Résiliation : {restaurant_name}",
                    "htmlContent": f"""<div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:24px">
<h2 style="color:#111827;margin:0 0 16px">Résiliation d'abonnement</h2>
<div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:12px;padding:16px;margin-bottom:16px">
<p style="margin:4px 0;font-size:14px"><strong>Restaurant :</strong> {restaurant_name}</p>
<p style="margin:4px 0;font-size:14px"><strong>Email :</strong> {user_email}</p>
<p style="margin:4px 0;font-size:14px"><strong>Fin effective :</strong> {effective_date}</p>
{reason_html}
<p style="margin:4px 0;font-size:13px;color:#6B7280"><strong>Date résiliation :</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
</div>
</div>""",
                }
            )
            logger.info(f"Cancellation emails sent for {restaurant_name}")
    except Exception as e:
        logger.error(f"Cancellation email error: {e}")


# ==============================================================
# AUTH ENDPOINTS
# ==============================================================
