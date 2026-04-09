"""
Admin panel.

Protezione: serve essere loggati + `is_admin=True`. Il primo utente
registrato nell'app viene promosso automaticamente ad admin (vedi
auth/routes.py register()), così in dev non serve configurare nulla.

In aggiunta, per gli endpoint JSON puri, accettiamo anche ?token=...
dove il valore deve corrispondere all'env var NETGOLF_ADMIN_TOKEN (vedi
config.yaml admin.token_env). Questo replica il comportamento di
server.js /api/admin/log e /api/admin/whitelist.
"""

from __future__ import annotations

from functools import wraps

from flask import current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import desc, select

from ..config import AppConfig
from ..db import db
from ..models import AccessLog, User
from . import bp


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Modalità 1: token in querystring (per integrazioni CLI/cron)
        cfg: AppConfig = current_app.config["NETGOLF"]
        token_env = cfg.admin_token()
        if token_env and request.args.get("token") == token_env:
            return fn(*args, **kwargs)

        # Modalità 2: utente loggato con is_admin
        if current_user.is_authenticated and current_user.is_admin:
            return fn(*args, **kwargs)

        return jsonify(error="Unauthorized"), 401

    return wrapper


@bp.get("")
@admin_required
def index():
    return render_template("admin/index.html")


@bp.get("/log")
@admin_required
def log():
    entries = db.session.scalars(
        select(AccessLog).order_by(desc(AccessLog.ts)).limit(500)
    ).all()
    return jsonify(
        count=len(entries),
        entries=[
            {
                "ts": e.ts.isoformat() if e.ts else None,
                "event": e.event,
                "email": e.email,
                "success": e.success,
                "reason": e.reason,
                "ip": e.ip,
                "user_agent": e.user_agent,
            }
            for e in entries
        ],
    )


@bp.get("/users")
@admin_required
def users():
    rows = db.session.scalars(select(User).order_by(User.created_at.desc())).all()
    return jsonify(
        users=[
            {
                "id": u.id,
                "email": u.email,
                "locale": u.locale,
                "is_admin": u.is_admin,
                "has_fig": u.has_fig_credentials,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": (
                    u.last_login_at.isoformat() if u.last_login_at else None
                ),
            }
            for u in rows
        ]
    )

@bp.route("/db-inspect")
@admin_required
def db_inspect():
    """
    Endpoint diagnostico TEMPORANEO. Restituisce un dump delle tabelle
    principali per verificare cosa è effettivamente persistito nel DB.
    DA RIMUOVERE dopo aver finito di debuggare.
    """
    import os
    from ..models import User, FigCredential, AccessLog
    from ..db import db

    db_path = db.engine.url.database
    db_size = 0
    if db_path and os.path.exists(db_path):
        db_size = os.path.getsize(db_path)

    users = []
    for u in User.query.order_by(User.id).limit(50):
        users.append({
            "id": u.id,
            "email": u.email,
            "is_admin": u.is_admin,
            "locale": u.locale,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "has_fig_credential": u.fig_credential is not None,
        })

    fig_creds = []
    for f in FigCredential.query.limit(50):
        fig_creds.append({
            "id": f.id,
            "user_id": f.user_id,
            "username_fig": f.username,
            "ciphertext_len": len(f.password_ciphertext) if f.password_ciphertext else 0,
            "nonce_len": len(f.password_nonce) if f.password_nonce else 0,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })

    last_logs = []
    for l in AccessLog.query.order_by(AccessLog.id.desc()).limit(20):
        last_logs.append({
            "ts": l.ts.isoformat() if l.ts else None,
            "event": l.event,
            "email": l.email,
            "success": l.success,
            "reason": l.reason,
            "ip": l.ip,
        })

    # Verifica anche la cartella del DB
    db_dir = os.path.dirname(db_path) if db_path else None
    dir_listing = []
    if db_dir and os.path.exists(db_dir):
        try:
            for name in os.listdir(db_dir):
                full = os.path.join(db_dir, name)
                dir_listing.append({
                    "name": name,
                    "size": os.path.getsize(full) if os.path.isfile(full) else None,
                    "is_dir": os.path.isdir(full),
                })
        except Exception as e:
            dir_listing = [{"error": str(e)}]

    return {
        "db_path": db_path,
        "db_dir": db_dir,
        "db_size_bytes": db_size,
        "db_dir_listing": dir_listing,
        "user_count": User.query.count(),
        "fig_credential_count": FigCredential.query.count(),
        "access_log_count": AccessLog.query.count(),
        "users": users,
        "fig_credentials": fig_creds,
        "last_20_access_logs": last_logs,
    }
