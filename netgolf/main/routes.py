"""
Rotte principali (landing, dashboard stub, health, config JSON).

La vera dashboard — con fetch profilo FIG, storico, grafici handicap, frase
obiettivo, calcolatore HCP di gioco — sarà montata sopra questo stub nella
seconda passata, quando i blueprint fig/ e gesgolf/ saranno pronti.
"""

from flask import current_app, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..config import AppConfig
from . import bp


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("main/dashboard.html")


@bp.route("/health")
def health():
    return jsonify(status="ok")


@bp.get("/api/config")
def api_config():
    """
    Config pubblica esposta al front-end (equivalente del vecchio
    /config.json di server.js). Contiene solo ciò che serve alla dashboard
    lato client — versione app, what's new, fasce HCP con colori.
    Nessun dato sensibile.
    """
    gs_cfg: AppConfig = current_app.config["NETGOLF"]
    return jsonify(
        app={
            "version": gs_cfg.raw.app.version,
            "name": gs_cfg.raw.app.name,
            "releaseDate": gs_cfg.raw.app.release_date,
            "whatsNew": gs_cfg.raw.app.whats_new,
        },
        hcpColors=[
            {
                "min": b.min,
                "max": b.max,
                "label": b.label_it,      # lato front-end la label è IT, i18n arriva via ?lang
                "label_en": b.label_en,
                "bg": b.bg,
                "accent": b.accent,
            }
            for b in gs_cfg.hcp_bands
        ],
    )
