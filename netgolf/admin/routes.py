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
@bp.route("/db-inspect")
def db_inspect():
    """
    Endpoint diagnostico TEMPORANEO. DA RIMUOVERE dopo il debug.
    """
    from flask import jsonify
    from flask_login import current_user
    import os

    if not current_user.is_authenticated:
        return jsonify(error="not authenticated"), 401
    if not getattr(current_user, "is_admin", False):
        return jsonify(error="not admin"), 403

    from ..models import User, FigCredential, AccessLog
    from ..db import db

    db_path = str(db.engine.url.database)
    db_size = 0
    if db_path and os.path.exists(db_path):
        try:
            db_size = os.path.getsize(db_path)
        except Exception as e:
            db_size = f"error: {e}"

    db_dir = os.path.dirname(db_path) if db_path else None
    dir_listing = []
    dir_error = None
    if db_dir and os.path.exists(db_dir):
        try:
            for name in os.listdir(db_dir):
                full = os.path.join(db_dir, name)
                try:
                    is_file = os.path.isfile(full)
                    sz = os.path.getsize(full) if is_file else None
                except Exception:
                    is_file, sz = False, None
                dir_listing.append({"name": name, "size": sz, "is_file": is_file})
        except Exception as e:
            dir_error = str(e)
    else:
        dir_error = "directory non esistente"

    try:
        user_count = User.query.count()
    except Exception as e:
        user_count = f"error: {e}"
    try:
        fig_count = FigCredential.query.count()
    except Exception as e:
        fig_count = f"error: {e}"
    try:
        log_count = AccessLog.query.count()
    except Exception as e:
        log_count = f"error: {e}"

    users = []
    try:
        for u in User.query.order_by(User.id).limit(20):
            users.append({
                "id": u.id,
                "email": u.email,
                "is_admin": bool(u.is_admin),
            })
    except Exception as e:
        users = [{"error": str(e)}]

    fig_creds = []
    try:
        for f in FigCredential.query.limit(20):
            fig_creds.append({
                "user_id": f.user_id,
                "username_fig": f.username,
                "has_ciphertext": bool(f.password_ciphertext),
            })
    except Exception as e:
        fig_creds = [{"error": str(e)}]

    logs = []
    try:
        for l in AccessLog.query.order_by(AccessLog.id.desc()).limit(10):
            logs.append({
                "ts": str(l.ts) if l.ts else None,
                "event": l.event,
                "email": l.email,
                "success": bool(l.success),
            })
    except Exception as e:
        logs = [{"error": str(e)}]

    return jsonify({
        "db_path": db_path,
        "db_dir": db_dir,
        "db_size_bytes": db_size,
        "db_dir_listing": dir_listing,
        "db_dir_error": dir_error,
        "user_count": user_count,
        "fig_credential_count": fig_count,
        "access_log_count": log_count,
        "users": users,
        "fig_credentials": fig_creds,
        "last_10_logs": logs,
    })
