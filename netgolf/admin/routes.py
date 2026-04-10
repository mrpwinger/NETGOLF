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

import os
from functools import wraps

from flask import current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import desc, select

from ..config import AppConfig
from ..db import db
from ..models import AccessLog, FigCredential, User
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


@bp.get("/access-log/tail")
def access_log_tail():
    """
    Restituisce le ultime N righe del file access.log come JSON.
    Default 100 righe, max 1000.
    """
    if not current_user.is_authenticated:
        return jsonify(error="not authenticated"), 401
    if not getattr(current_user, "is_admin", False):
        return jsonify(error="not admin"), 403

    n = min(int(request.args.get("n", 100)), 1000)
    db_path = str(db.engine.url.database)
    log_path = os.path.join(os.path.dirname(db_path), "access.log")

    if not os.path.exists(log_path):
        return jsonify(
            file=log_path,
            exists=False,
            lines=[],
            error="file non trovato (forse nessun evento è stato loggato ancora)",
        )

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        last = all_lines[-n:] if len(all_lines) > n else all_lines
        return jsonify(
            file=log_path,
            exists=True,
            total_lines=len(all_lines),
            returned_lines=len(last),
            size_bytes=os.path.getsize(log_path),
            lines=[line.rstrip("\n") for line in last],
        )
    except Exception as e:
        return jsonify(file=log_path, error=str(e)), 500


@bp.get("/access-log/download")
def access_log_download():
    """
    Scarica il file access.log intero come text/plain.
    Utile per analisi offline (grep, awk, ecc.).
    """
    from flask import Response

    if not current_user.is_authenticated:
        return Response("not authenticated", status=401)
    if not getattr(current_user, "is_admin", False):
        return Response("not admin", status=403)

    db_path = str(db.engine.url.database)
    log_path = os.path.join(os.path.dirname(db_path), "access.log")

    if not os.path.exists(log_path):
        return Response("access.log non esiste ancora", status=404, mimetype="text/plain")

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return Response(
            content,
            mimetype="text/plain",
            headers={
                "Content-Disposition": "attachment; filename=netgolf-access.log",
            },
        )
    except Exception as e:
        return Response(f"errore lettura: {e}", status=500, mimetype="text/plain")
