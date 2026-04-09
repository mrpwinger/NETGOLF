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
