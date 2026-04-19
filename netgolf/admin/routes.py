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

from flask import current_app, jsonify, redirect, render_template, render_template_string, request, url_for
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


# ── Aggiornamento campi_slope_cr.json da Excel FIG ───────────────────────────

_CAMPI_UPDATE_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <title>Aggiorna Campi — NETGOLF Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --green-accent:#00FF66;--green-light:#00cc52;
      --cream:#f0faf5;--white:#ffffff;--gray-soft:#6b8c7a;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{
      font-family:-apple-system,sans-serif;
      background:linear-gradient(150deg,#071a10 0%,#0a2a1a 40%,#112240 100%);
      color:var(--cream);min-height:100vh;padding:32px 16px;
    }
    .container{max-width:600px;margin:0 auto}
    .back{color:var(--gray-soft);text-decoration:none;font-size:13px;display:inline-block;margin-bottom:20px}
    .back:hover{color:var(--green-accent)}
    h1{font-size:22px;font-weight:800;color:var(--white);margin-bottom:6px}
    .sub{font-size:13px;color:var(--gray-soft);margin-bottom:28px}
    .card{
      background:rgba(10,92,54,0.1);border:1px solid rgba(0,255,102,0.15);
      border-radius:14px;padding:24px;margin-bottom:16px;
    }
    .card-title{font-size:11px;font-weight:700;letter-spacing:2px;color:var(--green-light);
      text-transform:uppercase;margin-bottom:16px}
    label{display:block;font-size:13px;color:var(--gray-soft);margin-bottom:6px}
    input[type=file]{
      display:block;width:100%;padding:10px 14px;border-radius:8px;font-size:13px;
      background:rgba(0,0,0,0.3);border:1px solid rgba(0,255,102,0.25);
      color:var(--cream);font-family:inherit;cursor:pointer;
    }
    input[type=file]::file-selector-button{
      background:rgba(0,255,102,0.15);border:1px solid rgba(0,255,102,0.3);
      color:var(--green-accent);border-radius:6px;padding:4px 12px;font-size:12px;
      cursor:pointer;margin-right:10px;font-family:inherit;
    }
    .btn-submit{
      margin-top:18px;width:100%;padding:12px;border-radius:10px;font-size:14px;
      font-weight:700;cursor:pointer;font-family:inherit;
      background:rgba(0,255,102,0.2);border:1px solid rgba(0,255,102,0.5);
      color:var(--green-accent);
    }
    .btn-submit:hover{background:rgba(0,255,102,0.3)}
    .flash{padding:10px 16px;border-radius:8px;font-size:13px;margin-bottom:16px}
    .flash-success{background:rgba(0,255,102,0.1);border:1px solid rgba(0,255,102,0.3);color:var(--green-accent)}
    .flash-error{background:rgba(255,100,100,0.12);border:1px solid rgba(255,100,100,0.35);color:#ff8a9e}
    .info-box{
      background:rgba(77,159,255,0.08);border:1px solid rgba(77,159,255,0.2);
      border-radius:10px;padding:14px 16px;font-size:12px;color:#8ac4ff;line-height:1.6;
      margin-bottom:16px;
    }
    .info-box strong{color:#b8d9ff}
  </style>
</head>
<body>
  <div class="container">
    <a href="/admin" class="back">← Admin</a>
    <h1>Aggiorna Campi FIG</h1>
    <p class="sub">Importa il file Excel ufficiale FIG per aggiornare CR, Slope e Par di tutti i percorsi.</p>

    {% with messages = get_flashed_messages(with_categories=true) %}
      {% for cat, msg in messages %}
        <div class="flash flash-{{ cat }}">{{ msg }}</div>
      {% endfor %}
    {% endwith %}

    <div class="info-box">
      <strong>Formato atteso:</strong> foglio Excel FIG con colonne
      Circolo · Percorso · PAR · poi coppie CR/Slope per ogni colore tee
      (NERO, BIANCO, GIALLO, VERDE, BLU, ROSSO, ARANCIO).<br><br>
      Il file <strong>campi_slope_cr.json</strong> corrente verrà salvato
      come backup prima della sostituzione.
    </div>

    <div class="card">
      <div class="card-title">Carica file Excel</div>
      <form method="post" enctype="multipart/form-data">
        <label for="excel_file">File .xlsx</label>
        <input type="file" id="excel_file" name="excel_file" accept=".xlsx,.xls">
        <button type="submit" class="btn-submit">↑ Importa e aggiorna JSON</button>
      </form>
    </div>
  </div>
</body>
</html>
"""


@bp.get("/campi/update")
@admin_required
def campi_update_form():
    """Pagina con form per caricare il file Excel dei campi FIG."""
    return render_template_string(_CAMPI_UPDATE_TEMPLATE)


@bp.post("/campi/update")
@admin_required
def campi_update():
    """Riceve il file Excel, lo converte e aggiorna campi_slope_cr.json."""
    from flask import flash
    from pathlib import Path

    f = request.files.get("excel_file")
    if not f or not f.filename:
        flash("Nessun file selezionato.", "error")
        return redirect(url_for("admin.campi_update_form"))

    if not f.filename.lower().endswith((".xlsx", ".xls")):
        flash("Il file deve essere un foglio Excel (.xlsx).", "error")
        return redirect(url_for("admin.campi_update_form"))

    try:
        from .excel_to_campi import update_campi_json_file

        excel_bytes = f.read()

        # Trova il percorso del JSON
        cfg: AppConfig = current_app.config.get("NETGOLF")
        if cfg and hasattr(cfg, "campi_slope_cr_path"):
            json_path = Path(cfg.campi_slope_cr_path)
        else:
            # Fallback: cerca nella root del progetto
            json_path = Path(current_app.root_path).parent / "campi_slope_cr.json"

        n_record, backup_path = update_campi_json_file(excel_bytes, json_path)

        msg = f"Aggiornamento completato: {n_record} percorsi importati."
        if backup_path:
            msg += f" Backup: {Path(backup_path).name}"
        flash(msg, "success")

    except ImportError as e:
        flash(
            f"Dipendenza mancante: {e}. "
            "Aggiungi 'pandas' e 'openpyxl' al requirements.txt.",
            "error",
        )
    except Exception as e:
        flash(f"Errore durante l'aggiornamento: {e}", "error")

    return redirect(url_for("admin.campi_update_form"))

@bp.get("/garmin-circoli-frequenti")
@admin_required
def garmin_circoli_frequenti():
    from netgolf.models import Scorecard
    from sqlalchemy import func
    rows = db.session.execute(
        db.select(Scorecard.circolo, func.count(Scorecard.id).label("n"))
        .where(Scorecard.source == "garmin", Scorecard.user_id == 1)
        .group_by(Scorecard.circolo)
        .order_by(func.count(Scorecard.id).desc())
        .limit(50)
    ).all()
    return jsonify(circoli=[{"circolo": r.circolo, "giri": r.n} for r in rows])
