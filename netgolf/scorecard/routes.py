"""
Rotte del blueprint scorecard.

Flusso utente (Fase 1):
    1. GET  /scorecard/upload         → form di upload (file o camera)
    2. POST /scorecard/upload         → riceve la foto, chiama OCR, mostra review
    3. POST /scorecard/confirm        → l'utente conferma → riepilogo finale
                                          (in Fase 2 salveremo nel DB)

I dati estratti dall'OCR vengono passati tra POST e review tramite la sessione
Flask (sono qualche KB di JSON, va benissimo). NON salviamo nulla nel DB
in Fase 1.
"""

from __future__ import annotations
import logging
from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from .ocr import (
    ScorecardImageError,
    ScorecardOCRConfigError,
    ScorecardOCRError,
    extract_scorecard,
)
from . import bp
from .storage import (
    save_scorecard,
    list_scorecards_for_user,
    get_scorecard,
    find_scorecard_for_gara,
    link_scorecard_to_fig,
    unlink_scorecard_from_fig,
)

logger = logging.getLogger(__name__)


# ─── Limiti upload ────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB (foto iPhone HEIC arrivano a ~5 MB)
ALLOWED_MIME_PREFIXES = ("image/",)


# ─── GET /scorecard/upload ────────────────────────────────────────────────


@bp.get("/upload")
@login_required
def upload_form():
    """Form di upload foto scorecard. Mostra anche eventuali errori della
    POST precedente passati via flash."""
    return render_template("scorecard/upload.html")


# ─── POST /scorecard/upload ───────────────────────────────────────────────


@bp.post("/upload")
@login_required
def upload_submit():
    """Riceve la foto, la passa a OCR, salva il risultato in sessione,
    redirect alla pagina di review."""

    # 1) Validazione presenza file.
    # Il form ha due <input name="scorecard_image"> (camera + galleria). Il JS
    # svuota quello non usato, ma Werkzeug può comunque vedere entrambi i campi:
    # prendiamo il primo FileStorage con un filename valido, ignorando i vuoti.
    files_list = request.files.getlist("scorecard_image")
    f = next(
        (x for x in files_list if x and x.filename and x.filename.strip()),
        None,
    )
    if f is None:
        flash("Nessun file selezionato. Scatta una foto o scegline una dalla galleria.", "error")
        return redirect(url_for("scorecard.upload_form"))

    # 2) Validazione tipo
    mime = (f.mimetype or "").lower()
    if not mime.startswith(ALLOWED_MIME_PREFIXES):
        flash(
            f"Il file deve essere un'immagine. Tipo ricevuto: {mime or 'sconosciuto'}",
            "error",
        )
        return redirect(url_for("scorecard.upload_form"))

    # 3) Lettura bytes (con limite di sicurezza)
    image_bytes = f.read(MAX_UPLOAD_BYTES + 1)
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        flash(
            f"Immagine troppo grande (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB). "
            "Usa una foto a risoluzione più bassa.",
            "error",
        )
        return redirect(url_for("scorecard.upload_form"))

    if not image_bytes:
        flash("File vuoto.", "error")
        return redirect(url_for("scorecard.upload_form"))

    # 4) OCR via Anthropic
    try:
        logger.info(
            "Scorecard OCR start: user=%s file=%s size=%d",
            current_user.email,
            f.filename,
            len(image_bytes),
        )
        parsed = extract_scorecard(image_bytes, filename=f.filename)
    except ScorecardOCRConfigError as e:
        logger.error("OCR config error: %s", e)
        flash(
            "Il servizio OCR non è configurato correttamente sul server. "
            "Contatta l'amministratore.",
            "error",
        )
        return redirect(url_for("scorecard.upload_form"))
    except ScorecardImageError as e:
        logger.warning("OCR image error: %s", e)
        flash(f"Immagine non valida: {e}", "error")
        return redirect(url_for("scorecard.upload_form"))
    except ScorecardOCRError as e:
        logger.error("OCR error: %s", e)
        flash(
            "Errore durante l'analisi della foto. Riprova tra qualche secondo. "
            "Se il problema persiste, controlla che la foto sia ben leggibile.",
            "error",
        )
        return redirect(url_for("scorecard.upload_form"))

    # 5) Validazione di dominio: ordini colpi devono essere permutazione 1..18
    domain_warnings = _domain_validate(parsed)
    if domain_warnings:
        # Aggiungi i warning di dominio a quelli già emessi dall'OCR
        existing = parsed.setdefault("warnings", []) or []
        parsed["warnings"] = existing + domain_warnings

    # 6) Confronto nome giocatore vs utente loggato
    nome_card = ((parsed.get("giocatore") or {}).get("nome_completo") or "").strip().upper()
    nome_utente = _get_user_full_name(current_user)
    nome_match = _names_match(nome_card, nome_utente)

    parsed["_meta"] = {
        "uploaded_filename": f.filename,
        "uploaded_size_bytes": len(image_bytes),
        "user_email": current_user.email,
        "user_full_name": nome_utente,
        "name_match": nome_match,
    }

    # 7) Salva in sessione e redirect a review
    session["scorecard_ocr_result"] = parsed
    return redirect(url_for("scorecard.review"))


# ─── GET /scorecard/review ────────────────────────────────────────────────


@bp.get("/review")
@login_required
def review():
    """Mostra i dati estratti in form modificabile."""
    parsed = session.get("scorecard_ocr_result")
    if not parsed:
        flash("Nessun risultato OCR in sessione. Carica una foto.", "info")
        return redirect(url_for("scorecard.upload_form"))

    return render_template("scorecard/review.html", data=parsed)


# ─── POST /scorecard/confirm ──────────────────────────────────────────────


@bp.post("/confirm")
@login_required
def confirm():
    """
    L'utente ha rivisto/corretto i dati e clicca Conferma.
    Fase 2: persistiamo nel DB con calcolo Stableford e match FK opzionale
    alla gara dello storico FIG.
    """
    parsed = session.get("scorecard_ocr_result")
    if not parsed:
        flash("Sessione scaduta. Ricarica la foto.", "error")
        return redirect(url_for("scorecard.upload_form"))
 
    # Applica le correzioni manuali dall'utente al dict parsed
    confirmed = _apply_user_corrections(parsed, request.form)
 
# Persisti
    try:
        from .storage import (
            colpi_ricevuti as calc_colpi,
            adjusted_gross_score,
            stableford_lordo,
            stableford_netto,
        )

        hcp_gioco = (confirmed.get("handicap") or {}).get("hcp_gioco")
        campo = confirmed.get("campo") or {}
        torneo = confirmed.get("torneo") or {}
        giocatore = confirmed.get("giocatore") or {}
        handicap = confirmed.get("handicap") or {}

        header = {
            "torneo_nome":        torneo.get("nome"),
            "data_gara":          torneo.get("data_gara"),
            "circolo":            campo.get("circolo"),
            "percorso":           campo.get("percorso"),
            "tee_colore":         campo.get("tee_colore"),
            "cr":                 campo.get("cr_uomini"),
            "sr":                 campo.get("sr_uomini"),
            "giocatore_nome":     giocatore.get("nome_completo"),
            "giocatore_tessera":  giocatore.get("tessera"),
            "hcp_index":          handicap.get("hcp_index"),
            "hcp_gioco":          handicap.get("hcp_gioco"),
            "stbl_lordo_totale":  0,
            "stbl_netto_totale":  0,
            "score_lordo_totale": 0,
            "ags_totale":         0,
        }

        holes = []
        for b in (confirmed.get("buche") or []):
            par       = b.get("par")
            ordine    = b.get("ordine_colpi")
            score_raw = b.get("score")
            colpi     = calc_colpi(hcp_gioco, ordine)
            ags       = adjusted_gross_score(par, score_raw, colpi)
            stbl_l    = stableford_lordo(par, ags)
            stbl_n    = stableford_netto(par, ags, colpi)

            holes.append({
                "buca":           b.get("buca"),
                "par":            par,
                "metri_uomini":   b.get("metri_uomini"),
                "ordine_colpi":   ordine,
                "score_raw":      str(score_raw) if score_raw is not None else None,
                "score_ags":      ags,
                "colpi_ricevuti": colpi,
                "stbl_lordo":     stbl_l,
                "stbl_netto":     stbl_n,
            })

            if isinstance(score_raw, int):
                header["score_lordo_totale"] += score_raw
            if ags is not None:
                header["ags_totale"] += ags
            header["stbl_lordo_totale"] += stbl_l
            header["stbl_netto_totale"] += stbl_n

        sc = save_scorecard(current_user.id, header, holes)

        # Auto-match con storico FIG live (non bloccante)
        try:
            from .storage import match_scorecard_to_storico
            from netgolf.fig.service import FigService
            from netgolf.fig.client import FigError

            service = FigService.from_app()
            storico_data = service.fetch_storico(current_user)
            fig = match_scorecard_to_storico(
                user_id=current_user.id,
                scorecard_data_gara=sc.data_gara,
                scorecard_circolo=sc.circolo,
                storico_results=storico_data.get("results", []),
            )
            if fig:
                sc.fig_result_id = fig.id
                from netgolf.db import db
                db.session.commit()
                flash(_("Scorecard collegata automaticamente alla gara FIG del %(data)s.", data=sc.data_gara), "info")
        except Exception as e:
            logger.warning("Auto-match FIG fallito (non bloccante): %s", e)

    except Exception as e:
        logger.exception("Errore salvataggio scorecard: %s", e)
        flash(f"Errore durante il salvataggio: {e}", "error")
        return redirect(url_for("scorecard.upload_form"))

    # Pulisci la sessione OCR
    session.pop("scorecard_ocr_result", None)

    return redirect(url_for("scorecard.detail", scorecard_id=sc.id))


# ─── Helper privati ───────────────────────────────────────────────────────


def _domain_validate(parsed: dict) -> list[str]:
    """
    Validazioni di dominio (non OCR): ordine colpi univoco, totali coerenti, ecc.
    Ritorna una lista di warning testuali da mostrare all'utente.
    """
    warnings: list[str] = []

    buche = parsed.get("buche") or []
    if not isinstance(buche, list):
        return warnings

    # Ordine colpi: deve essere permutazione di 1..18
    ordini = [b.get("ordine_colpi") for b in buche if isinstance(b, dict)]
    ordini_validi = [o for o in ordini if isinstance(o, int) and 1 <= o <= 18]
    duplicati = sorted({o for o in ordini_validi if ordini_validi.count(o) > 1})
    if duplicati:
        warnings.append(
            f"L'ordine colpi contiene valori duplicati ({', '.join(map(str, duplicati))}). "
            "Verifica le buche corrispondenti."
        )
    mancanti = sorted(set(range(1, 19)) - set(ordini_validi))
    if mancanti and len(ordini_validi) >= 16:
        # Solo se ne ha letti quasi tutti — altrimenti il messaggio è ridondante
        warnings.append(
            f"Mancano nell'ordine colpi i valori: {', '.join(map(str, mancanti))}."
        )

    # Coerenza somma score buche 1-9 (info, non bloccante)
    score_out = sum(
        b.get("score") for b in buche[:9]
        if isinstance(b.get("score"), int)
    )
    out_par = (parsed.get("totali_stampati") or {}).get("out_par")
    if out_par and score_out and score_out < out_par - 5:
        # Score troppo basso rispetto al par — sospetto che sia stato letto
        # qualcosa al posto di altro
        warnings.append(
            f"Score sommato delle prime 9 buche ({score_out}) è molto inferiore al par OUT ({out_par})."
        )

    return warnings


def _get_user_full_name(user) -> str | None:
    """Ricava il nome completo dell'utente dal suo profilo FIG già caricato.
    Se NETGOLF non lo conosce ancora, ritorna None."""
    cred = getattr(user, "fig_credential", None)
    if not cred:
        return None
    # Best effort: NETGOLF non salva il nome FIG nel DB User, quindi
    # qui ritorniamo None. La review UI mostrerà comunque il nome
    # estratto dalla scorecard, e l'utente saprà se è il suo o no.
    return None


def _names_match(name_card: str | None, name_user: str | None) -> bool | None:
    """True se i nomi combaciano, False se no, None se non possiamo decidere."""
    if not name_card or not name_user:
        return None
    return name_card.strip().upper() == name_user.strip().upper()


def _apply_user_corrections(parsed: dict, form) -> dict:
    """
    Applica le correzioni inviate dal form di review al dict OCR parsed.
    Form fields attesi (nomi):
        torneo_nome, torneo_data_gara
        giocatore_nome_completo, giocatore_tessera
        campo_circolo, campo_percorso, campo_tee_colore
        campo_cr_uomini, campo_sr_uomini
        handicap_hcp_index, handicap_hcp_gioco
        buca_<n>_score    per n in 1..18
    """
    out = dict(parsed)  # shallow copy

    # Sezioni semplici
    for section, field in [
        ("torneo", "nome"),
        ("torneo", "data_gara"),
        ("giocatore", "nome_completo"),
        ("giocatore", "tessera"),
        ("campo", "circolo"),
        ("campo", "percorso"),
        ("campo", "tee_colore"),
        ("campo", "cr_uomini"),
        ("campo", "sr_uomini"),
        ("handicap", "hcp_index"),
        ("handicap", "hcp_gioco"),
    ]:
        form_key = f"{section}_{field}"
        if form_key in form:
            value = form.get(form_key, "").strip() or None
            section_dict = out.setdefault(section, {}) or {}
            # Cast numerici
            if value is not None and field in ("cr_uomini", "hcp_index"):
                try:
                    value = float(value.replace(",", "."))
                except ValueError:
                    pass
            elif value is not None and field in ("sr_uomini", "hcp_gioco"):
                try:
                    value = int(value)
                except ValueError:
                    pass
            section_dict[field] = value
            out[section] = section_dict

    # Score per buca
    buche = out.get("buche") or []
    for i, buca in enumerate(buche):
        if not isinstance(buca, dict):
            continue
        n = buca.get("buca", i + 1)
        form_key = f"buca_{n}_score"
        if form_key in form:
            raw = form.get(form_key, "").strip()
            if not raw:
                buca["score"] = None
            elif raw.upper() == "X":
                buca["score"] = "X"
            else:
                try:
                    buca["score"] = int(raw)
                except ValueError:
                    buca["score"] = raw  # lascia stringa, l'utente vedrà
    out["buche"] = buche

    return out

@bp.get("/list")
@login_required
def list_view():
    """Lista delle scorecard caricate dall'utente, più recenti prima."""
    cards = list_scorecards_for_user(current_user.id)
    return render_template("scorecard/list.html", cards=cards)
 
 
@bp.get("/<int:scorecard_id>")
@login_required
def detail(scorecard_id: int):
    sc = get_scorecard(scorecard_id, current_user.id)
    if not sc:
        abort(404)

    fig_results_candidati = []
    if not sc.fig_result_id:
        try:
            from netgolf.fig.service import FigService
            service = FigService.from_app()
            storico_data = service.fetch_storico(current_user)
            from .storage import _date_fig_to_iso
            raw_results = storico_data.get("results", [])
            # Costruisci lista di dict semplici per il template
            seen = set()
            for r in raw_results:
                data_iso = _date_fig_to_iso(r.get("data", ""))
                circolo = r.get("esecutore", "") or r.get("gara", "")
                key = (data_iso, circolo)
                if data_iso and circolo and key not in seen:
                    seen.add(key)
                    fig_results_candidati.append({
                        "data_gara": data_iso,
                        "circolo": circolo,
                        "nome_torneo": r.get("gara"),
                        # encode come stringa per passarlo al form
                        "_key": f"{data_iso}|{circolo}|{r.get('gara', '')}",
                    })
        except Exception as e:
            logger.warning("Fetch storico FIG per candidati fallito: %s", e)

    return render_template(
        "scorecard/detail.html",
        sc=sc,
        fig_results_candidati=fig_results_candidati,
    )
 
 
@bp.get("/lookup")
@login_required
def lookup():
    """
    Endpoint JSON usato dal dashboard.js per sapere se per una certa
    coppia (data, circolo) dello storico FIG esiste una scorecard caricata.
 
    Query string:
        ?data=YYYY-MM-DD&circolo=NOMECIRCOLO
 
    Risposta:
        { "exists": true,  "scorecard_id": 42 }
        oppure
        { "exists": false }
    """
    data_gara = (request.args.get("data") or "").strip()
    circolo = (request.args.get("circolo") or "").strip()
    sc = find_scorecard_for_gara(current_user, data_gara, circolo)
    if sc:
        return jsonify(exists=True, scorecard_id=sc.id, stbl_lordo=sc.stbl_lordo_totale,
                       stbl_netto=sc.stbl_netto_totale, score_lordo=sc.score_lordo_totale)
    return jsonify(exists=False)

@bp.post("/<int:scorecard_id>/link")
@login_required
def link_fig(scorecard_id: int):
    key = request.form.get("fig_result_key", "").strip()
    if not key or key.count("|") < 2:
        flash(_("Seleziona una gara FIG."), "error")
        return redirect(url_for("scorecard.detail", scorecard_id=scorecard_id))

    parts = key.split("|", 2)
    data_iso, circolo, nome_torneo = parts[0], parts[1], parts[2]

    from .storage import find_or_create_fig_result
    fig = find_or_create_fig_result(
        user_id=current_user.id,
        data_gara_iso=data_iso,
        circolo=circolo,
        nome_torneo=nome_torneo or None,
    )

    sc = get_scorecard(scorecard_id, current_user.id)
    if not sc:
        abort(404)

    sc.fig_result_id = fig.id
    from netgolf.db import db
    db.session.commit()

    flash(_("Scorecard collegata alla gara FIG."), "success")
    return redirect(url_for("scorecard.detail", scorecard_id=scorecard_id))


@bp.post("/<int:scorecard_id>/unlink")
@login_required
def unlink_fig(scorecard_id: int):
    """Scollega la scorecard dalla gara FIG (la scorecard resta intatta)."""
    ok = unlink_scorecard_from_fig(scorecard_id, current_user.id)
    if ok:
        flash(_("Scorecard scollegata dalla gara FIG."), "success")
    else:
        flash(_("Scorecard non trovata."), "error")
    return redirect(url_for("scorecard.detail", scorecard_id=scorecard_id))
