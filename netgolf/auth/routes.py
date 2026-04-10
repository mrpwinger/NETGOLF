"""
Viste del blueprint auth.

Flusso NETGOLF:
  - GET/POST /auth/register → nuovo account (email + password).
  - GET/POST /auth/login    → login con email + password.
  - GET      /auth/logout   → logout.
  - GET/POST /auth/profilo  → anagrafica NETGOLF + gestione credenziali FIG.
  - GET      /auth/lang/<lang> → switch lingua via cookie.

Le credenziali FIG sono opzionali: se mancano, la dashboard NETGOLF continua
a funzionare mostrando solo ciò che è in cache locale (o niente, al primo
accesso). Quando l'utente le salva, vengono cifrate con AES-GCM usando la
master key del server (vedi crypto.FigCredentialCipher).
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import (
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from ..crypto import CipherBlob, FigCredentialCipher, PasswordService
from ..db import db
from ..models import AccessLog, FigCredential, User
from . import bp
from .forms import FigCredentialsForm, LoginForm, RegisterForm


# Logger dedicato agli eventi di accesso. I suoi messaggi vengono
# intercettati dal RotatingFileHandler configurato in netgolf/__init__.py
# e scritti su /app/data/runtime/access.log. Se il file handler non è
# configurato (es. boot in dev), i messaggi si perdono silenziosamente —
# il DB resta comunque la fonte di verità.
_access_log = logging.getLogger("netgolf.access")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _get_password_service() -> PasswordService:
    return current_app.extensions["netgolf_password_service"]


def _get_fig_cipher() -> FigCredentialCipher:
    return current_app.extensions["netgolf_fig_cipher"]


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("X-Real-Ip", request.remote_addr or "unknown")


def _log_event(event: str, success: bool, reason: str | None, user: User | None, email: str | None) -> None:
    entry = AccessLog(
        event=event,
        success=success,
        reason=reason,
        user_id=user.id if user else None,
        email=email or (user.email if user else None),
        ip=_client_ip(),
        user_agent=request.headers.get("User-Agent", "")[:500],
    )
    db.session.add(entry)

    # Rotazione: teniamo solo le ultime N entry come faceva server.js
    cfg = current_app.config["NETGOLF"].raw.access_log
    total = db.session.scalar(select(db.func.count()).select_from(AccessLog))
    if total and total > cfg.max_entries:
        to_drop = total - cfg.max_entries
        old_ids = db.session.scalars(
            select(AccessLog.id).order_by(AccessLog.ts.asc()).limit(to_drop)
        ).all()
        if old_ids:
            db.session.execute(
                db.delete(AccessLog).where(AccessLog.id.in_(old_ids))
            )
    db.session.commit()

    # Scrivi anche sul file di log (oltre al DB). Non blocca mai il flusso:
    # se il logger non è configurato o il filesystem è read-only, il
    # messaggio si perde silenziosamente senza rompere la request.
    try:
        _access_log.info(
            "%s  %s  %s  email=%s  ip=%s  ua=%r  reason=%s",
            event,
            "OK  " if success else "FAIL",
            f"user_id={user.id}" if user else "user_id=-",
            email or (user.email if user else "-"),
            _client_ip(),
            (request.headers.get("User-Agent", "-"))[:80],
            reason or "-",
        )
    except Exception:
        pass


# ─── Register ────────────────────────────────────────────────────────────────


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        existing = db.session.scalar(select(User).where(User.email == email))
        if existing:
            flash(_("Un account con questa email esiste già."), "error")
            _log_event("register", False, "email già registrata", None, email)
            return render_template("auth/register.html", form=form), 400

        pwd_service = _get_password_service()
        user = User(
            email=email,
            pwd_hash=pwd_service.hash(form.password.data),
            locale=current_app.config["NETGOLF"].raw.i18n.default_locale,
        )
        # Il primo utente diventa admin in automatico (comodo in dev)
        if db.session.scalar(select(db.func.count()).select_from(User)) == 0:
            user.is_admin = True

        db.session.add(user)
        db.session.commit()
        _log_event("register", True, "account creato", user, email)

        login_user(user)
        flash(_("Benvenuto in NETGOLF!"), "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/register.html", form=form)


# ─── Login ───────────────────────────────────────────────────────────────────


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(select(User).where(User.email == email))
        pwd_service = _get_password_service()

        if not user or not pwd_service.verify(user.pwd_hash, form.password.data):
            _log_event("login_netgolf", False, "credenziali non valide", user, email)
            flash(_("Email o password non corretti."), "error")
            return render_template("auth/login.html", form=form), 401

        # Re-hash opportunistico se Argon2 ha parametri nuovi
        if pwd_service.needs_rehash(user.pwd_hash):
            user.pwd_hash = pwd_service.hash(form.password.data)

        user.last_login_at = datetime.utcnow()
        db.session.commit()

        login_user(user, remember=form.remember.data)
        _log_event("login_netgolf", True, "login riuscito", user, email)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


# ─── Logout ──────────────────────────────────────────────────────────────────


@bp.route("/logout")
@login_required
def logout():
    _log_event("logout", True, None, current_user, current_user.email)
    logout_user()
    return redirect(url_for("auth.login"))


# ─── Profilo + credenziali FIG ──────────────────────────────────────────────


@bp.route("/profilo", methods=["GET", "POST"])
@login_required
def profilo():
    form = FigCredentialsForm()

    # Pre-popoliamo solo la tessera (mai la password: non la rimostriamo mai).
    if request.method == "GET" and current_user.fig_credential:
        form.tessera.data = current_user.fig_credential.tessera

    if form.validate_on_submit():
        # Caso 1: rimozione esplicita
        if form.remove.data and current_user.fig_credential:
            db.session.delete(current_user.fig_credential)
            db.session.commit()
            flash(_("Credenziali FIG rimosse."), "success")
            return redirect(url_for("auth.profilo"))

        tessera = (form.tessera.data or "").strip()
        password_fig = form.password_fig.data or ""

        # Caso 2: niente tessera e niente password → no-op
        if not tessera and not password_fig and not current_user.fig_credential:
            flash(_("Nessuna credenziale FIG da salvare."), "info")
            return redirect(url_for("auth.profilo"))

        # Caso 3: aggiornamento parziale (cambia solo tessera, password resta)
        existing = current_user.fig_credential
        if existing and tessera and not password_fig:
            existing.tessera = tessera
            existing.updated_at = datetime.utcnow()
            db.session.commit()
            flash(_("Numero tessera aggiornato."), "success")
            return redirect(url_for("auth.profilo"))

        # Caso 4: password fornita → serve anche la tessera
        if password_fig and not tessera:
            if existing:
                tessera = existing.tessera
            else:
                flash(_("Per salvare la password devi indicare anche il numero tessera."), "error")
                return render_template("auth/profilo.html", form=form), 400

        # Caso 5: creazione o aggiornamento completo
        if password_fig:
            cipher = _get_fig_cipher()
            blob = cipher.encrypt(password_fig, user_id=current_user.id)
            if existing:
                existing.tessera = tessera
                existing.password_ciphertext = blob.ciphertext_b64
                existing.password_nonce = blob.nonce_b64
                existing.updated_at = datetime.utcnow()
            else:
                db.session.add(
                    FigCredential(
                        user_id=current_user.id,
                        tessera=tessera,
                        password_ciphertext=blob.ciphertext_b64,
                        password_nonce=blob.nonce_b64,
                    )
                )
            db.session.commit()
            flash(_("Credenziali FIG salvate. Sono cifrate a riposo."), "success")
            return redirect(url_for("auth.profilo"))

    return render_template("auth/profilo.html", form=form)


# ─── Switch lingua ──────────────────────────────────────────────────────────


@bp.route("/lang/<lang>")
def set_language(lang: str):
    """
    Cambia la lingua corrente e la persiste via cookie.
    Se l'utente è loggato, aggiorna anche User.locale così la scelta
    sopravvive al prossimo login da un altro device.
    """
    cfg = current_app.config["NETGOLF"].raw.i18n
    if lang not in cfg.supported_locales:
        return redirect(request.referrer or url_for("main.index"))

    if current_user.is_authenticated:
        current_user.locale = lang
        db.session.commit()

    resp = make_response(redirect(request.referrer or url_for("main.index")))
    resp.set_cookie(
        cfg.cookie_name,
        lang,
        max_age=cfg.cookie_max_age_days * 24 * 3600,
        httponly=False,  # il JS del front-end può volerlo leggere per mostrare il flag
        samesite="Lax",
    )
    return resp


# Helper esposto per il service layer FIG: decifra la password FIG dell'utente
# in memoria (richiede master key del server). Ritorna (tessera, password) o None.
def get_fig_credentials_plain(user: User) -> tuple[str, str] | None:
    cred = user.fig_credential
    if not cred:
        return None
    cipher = _get_fig_cipher()
    blob = CipherBlob(
        ciphertext_b64=cred.password_ciphertext,
        nonce_b64=cred.password_nonce,
    )
    password = cipher.decrypt(blob, user_id=user.id)
    return cred.tessera, password
