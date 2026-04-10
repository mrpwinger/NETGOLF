"""
Rotte principali (landing, dashboard stub, health, config JSON).

La vera dashboard — con fetch profilo FIG, storico, grafici handicap, frase
obiettivo, calcolatore HCP di gioco — sarà montata sopra questo stub nella
seconda passata, quando i blueprint fig/ e gesgolf/ saranno pronti.
"""

from datetime import datetime
from zoneinfo import ZoneInfo  # stdlib Python 3.9+
 
from flask import current_app, render_template, abort
from flask_login import login_required, current_user
 
from ..fig.client import FigError, FigLoginFailed, FigSessionExpired
from ..fig.service import FigCredentialsMissing, FigService

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
@bp.route("/tessera")
@login_required
def tessera():
    # Scarica il profilo FIG fresco usando lo stesso service che già alimenta
    # /api/fig/profilo. Se le credenziali mancano o FIG dà errore, mostriamo
    # un errore HTTP comprensibile invece di esplodere con un 500.
    try:
        service = FigService.from_app()
        profilo = service.fetch_profilo(current_user)
    except FigCredentialsMissing:
        abort(412, description="Credenziali FIG non configurate. Vai su Profilo per inserirle.")
    except FigLoginFailed as e:
        abort(401, description=f"Login FIG fallito: {e}")
    except FigSessionExpired:
        abort(401, description="Sessione FIG scaduta, riprova.")
    except FigError as e:
        current_app.logger.warning("Errore FIG durante generazione tessera: %s", e)
        abort(502, description=f"Errore comunicazione FIG: {e}")
 
    if not profilo:
        abort(502, description="FederGolf ha restituito un profilo vuoto.")
 
    # Estrai i tre campi che servono. Le chiavi sono quelle parsate dai
    # profile_patterns / profile_labels in config.yaml -> sezione fig.
    nome = (profilo.get("nome") or "").strip()
    cognome = (profilo.get("cognome") or "").strip()
    nome_completo = f"{nome} {cognome}".strip().upper() or "—"
 
    tessera_num = (
        profilo.get("tessera")
        or (current_user.fig_credential.tessera if current_user.fig_credential else None)
        or "—"
    )
 
    # Il profilo FIG potrebbe esporre il campo come "handicap_index" (snake_case
    # dalla nostra config) oppure "handicapIndex" (camelCase, se il service
    # ricarica i dati con la convenzione vecchia di server.js). Coperti entrambi.
    hcp_index = (
        profilo.get("handicap_index")
        or profilo.get("handicapIndex")
        or "—"
    )
 
    # Anno corrente e timestamp di generazione, in fuso Europe/Rome
    # (lo stesso usato in config.yaml -> access_log.timezone, per coerenza
    # con il resto degli eventi loggati).
    cfg = current_app.config["NETGOLF"]
    tz_name = cfg.raw.access_log.timezone or "Europe/Rome"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Rome")
 
    now = datetime.now(tz)
    anno = now.year
    generato_il = now.strftime("%d/%m/%Y %H:%M") + f" ({tz_name})"
 
    return render_template(
        "tessera.html",
        nome_completo=nome_completo,
        tessera=tessera_num,
        hcp_index=hcp_index,
        anno=anno,
        generato_il=generato_il,
    )
 
