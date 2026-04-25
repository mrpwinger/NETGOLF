"""
Admin panel.
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
        cfg: AppConfig = current_app.config["NETGOLF"]
        token_env = cfg.admin_token()
        if token_env and request.args.get("token") == token_env:
            return fn(*args, **kwargs)
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
    if not current_user.is_authenticated:
        return jsonify(error="not authenticated"), 401
    if not getattr(current_user, "is_admin", False):
        return jsonify(error="not admin"), 403

    n = min(int(request.args.get("n", 100)), 1000)
    db_path = str(db.engine.url.database)
    log_path = os.path.join(os.path.dirname(db_path), "access.log")

    if not os.path.exists(log_path):
        return jsonify(file=log_path, exists=False, lines=[],
                       error="file non trovato")

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        last = all_lines[-n:] if len(all_lines) > n else all_lines
        return jsonify(
            file=log_path, exists=True,
            total_lines=len(all_lines), returned_lines=len(last),
            size_bytes=os.path.getsize(log_path),
            lines=[line.rstrip("\n") for line in last],
        )
    except Exception as e:
        return jsonify(file=log_path, error=str(e)), 500


@bp.get("/access-log/download")
def access_log_download():
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
            content, mimetype="text/plain",
            headers={"Content-Disposition": "attachment; filename=netgolf-access.log"},
        )
    except Exception as e:
        return Response(f"errore lettura: {e}", status=500, mimetype="text/plain")


# ── Aggiornamento campi_slope_cr.json da Excel FIG ───────────────────────────

@bp.get("/campi/update")
@admin_required
def campi_update_form():
    return render_template("admin/campi_update.html")


@bp.post("/campi/update")
@admin_required
def campi_update():
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
        cfg: AppConfig = current_app.config.get("NETGOLF")
        if cfg and hasattr(cfg, "campi_slope_cr_path"):
            json_path = Path(cfg.campi_slope_cr_path)
        else:
            json_path = Path(current_app.root_path).parent / "campi_slope_cr.json"

        n_record, backup_path = update_campi_json_file(excel_bytes, json_path)
        msg = f"Aggiornamento completato: {n_record} percorsi importati."
        if backup_path:
            msg += f" Backup: {Path(backup_path).name}"
        flash(msg, "success")

    except ImportError as e:
        flash(f"Dipendenza mancante: {e}.", "error")
    except Exception as e:
        flash(f"Errore: {e}", "error")

    return redirect(url_for("admin.campi_update_form"))


# ── Hole19 Stroke Index Scraper ───────────────────────────────────────────────

@bp.get("/hole19/scrape")
@admin_required
def hole19_scrape_form():
    return render_template("admin/hole19_scrape.html")


@bp.post("/hole19/scrape")
@admin_required
def hole19_scrape():
    import json
    from pathlib import Path

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        return jsonify(error=f"Dipendenza mancante: {e}."), 500

    url = request.form.get("url", "").strip()
    circolo = request.form.get("circolo", "").strip()
    percorso = request.form.get("percorso", "").strip()

    if not url or not circolo or not percorso:
        return jsonify(error="URL, circolo e percorso sono obbligatori."), 400

    try:
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        }
        r = requests.get(url, headers=hdrs, timeout=20)
        if r.status_code != 200:
            return jsonify(error=f"Hole19 ha risposto {r.status_code}."), 502

        soup = BeautifulSoup(r.text, "html.parser")
        holes_data = {}

        tables = soup.find_all("table")
        for table in tables:
            header_row = table.find("tr")
            if not header_row:
                continue
            header_cols = header_row.find_all(["th", "td"])
            si_idx = None
            for idx, h in enumerate(header_cols):
                txt = h.get_text(strip=True).upper()
                if "S.I" in txt or txt == "SI" or txt == "HCP":
                    si_idx = idx
                    break
            if si_idx is None:
                continue
            for row in table.find_all("tr")[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) <= si_idx:
                    continue
                try:
                    buca_num = int(cols[0].get_text(strip=True))
                    si_val = cols[si_idx].get_text(strip=True)
                    if si_val.isdigit():
                        holes_data[buca_num] = int(si_val)
                except (ValueError, IndexError):
                    continue

        if not holes_data:
            return jsonify(
                error="Nessun dato S.I. trovato. Verifica URL e che la pagina mostri il segnapunti."
            ), 404

        hcp_list = [holes_data.get(i) for i in range(1, 19)]

        cfg = current_app.config.get("NETGOLF")
        if cfg and hasattr(cfg, "campi_slope_cr_path"):
            json_path = Path(cfg.campi_slope_cr_path)
        else:
            json_path = Path(current_app.root_path).parent / "campi_slope_cr.json"

        with open(json_path, "r", encoding="utf-8") as f:
            records = json.load(f)

        updated = 0
        for rec in records:
            if (rec.get("circolo", "").upper() == circolo.upper() and
                    rec.get("percorso", "").upper() == percorso.upper()):
                rec["hcp"] = hcp_list
                updated += 1

        if updated == 0:
            return jsonify(
                warning=f"Dati trovati ma nessun percorso nel JSON per '{circolo}' / '{percorso}'.",
                holes_data=holes_data,
                hcp_list=hcp_list,
            )

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        return jsonify(
            ok=True,
            message=f"Stroke index salvato per {circolo} - {percorso}.",
            holes_data=holes_data,
            hcp_list=hcp_list,
            records_updated=updated,
        )

    except Exception as e:
        return jsonify(error=f"Errore scraping: {e}"), 500
