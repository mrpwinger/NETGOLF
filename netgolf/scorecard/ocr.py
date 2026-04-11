"""
Modulo OCR scorecard via Anthropic Claude vision.

Flusso:
    1. L'utente upload una foto (JPG/PNG/HEIC) tramite la rotta /scorecard/upload.
    2. extract_scorecard() apre l'immagine, la normalizza in JPEG (gestendo HEIC),
       la ridimensiona se troppo grande (Anthropic accetta max ~5 MB di payload
       totale ma prima dei limiti vige il buon senso: ridurre a max 1568px sul
       lato lungo dimezza la latenza senza perdere leggibilità sui numeri).
    3. La passa a Claude Sonnet con il prompt strutturato di prompt.py.
    4. Parsa la risposta JSON e la valida sommariamente.
    5. Ritorna un dict pronto per il template di review.

Errori sollevati (mappati nelle rotte come HTTP errors):
    - ScorecardOCRConfigError: ANTHROPIC_API_KEY mancante.
    - ScorecardImageError: il file in upload non è un'immagine valida o
      non è leggibile.
    - ScorecardOCRError: problema di rete, rate limit, o risposta malformata
      dall'API Anthropic.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any

from PIL import Image

from .prompt import PROMPT_OCR_SCORECARD

logger = logging.getLogger(__name__)


# ─── Eccezioni ────────────────────────────────────────────────────────────


class ScorecardOCRConfigError(Exception):
    """ANTHROPIC_API_KEY non configurata sul server."""


class ScorecardImageError(Exception):
    """Il file uploadato non è un'immagine valida o leggibile."""


class ScorecardOCRError(Exception):
    """Errore lato API Anthropic (rete, parsing, rate limit, ecc.)."""


# ─── Costanti ─────────────────────────────────────────────────────────────

# Modello Anthropic da usare. Sonnet 4.6 è la scelta consigliata: bilanciato
# qualità/costo, ottime capacità vision. Configurabile via env var per
# permettere upgrade/downgrade senza ridepoyare codice.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Lato lungo massimo a cui ridimensioniamo l'immagine prima di inviarla.
# 1568px è il sweet spot che Anthropic stessa raccomanda per task di
# document understanding: oltre, la latenza cresce molto e l'accuracy
# non migliora. Sotto, i numeri scritti a mano perdono dettaglio.
MAX_LONG_EDGE_PX = 1568

# Soglia di sicurezza per il payload base64 (Anthropic limita a 5 MB).
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024  # 4 MB di margine


# ─── Setup HEIC support (best-effort) ─────────────────────────────────────

# pillow-heif aggiunge il decoder HEIC/HEIF a Pillow. È una pip install
# normale, ma alcuni ambienti potrebbero non averla. Se manca, i caricamenti
# da iPhone falliranno con un messaggio chiaro invece di crashare.
try:
    from pillow_heif import register_heif_opener  # type: ignore
    register_heif_opener()
    _HEIC_SUPPORT = True
except ImportError:
    _HEIC_SUPPORT = False
    logger.warning(
        "pillow-heif non installato: le foto HEIC dagli iPhone non saranno "
        "decodificabili. Aggiungi 'pillow-heif' al requirements.txt."
    )


# ─── Funzioni pubbliche ───────────────────────────────────────────────────


def extract_scorecard(image_bytes: bytes, filename: str = "") -> dict[str, Any]:
    """
    Estrae i dati strutturati da una foto di scorecard.

    Args:
        image_bytes: contenuto raw del file uploadato.
        filename: nome file originale (per log).

    Returns:
        dict con la struttura definita in prompt.PROMPT_OCR_SCORECARD.

    Raises:
        ScorecardOCRConfigError, ScorecardImageError, ScorecardOCRError.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ScorecardOCRConfigError(
            "Variabile d'ambiente ANTHROPIC_API_KEY non configurata sul server."
        )

    # 1) Apri e normalizza l'immagine
    jpeg_bytes = _normalize_image(image_bytes, filename)

    # 2) Chiama Anthropic
    raw_json_text = _call_anthropic_vision(jpeg_bytes, api_key)

    # 3) Parsa la risposta
    parsed = _parse_anthropic_response(raw_json_text)

    # 4) Validazione minima di struttura (non blocca, solo log)
    _validate_structure(parsed)

    return parsed


# ─── Helper privati ───────────────────────────────────────────────────────


def _normalize_image(image_bytes: bytes, filename: str) -> bytes:
    """
    Apre un'immagine in qualunque formato supportato (JPG, PNG, WebP, HEIC),
    la converte a RGB, la ridimensiona se troppo grande, e restituisce
    JPEG bytes pronti per l'invio ad Anthropic.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        ext_hint = ""
        if filename.lower().endswith((".heic", ".heif")) and not _HEIC_SUPPORT:
            ext_hint = " (manca pillow-heif sul server per leggere HEIC)"
        raise ScorecardImageError(
            f"Impossibile aprire l'immagine '{filename}': {e}{ext_hint}"
        )

    # Auto-orient via EXIF (le foto da iPhone vengono spesso ruotate via EXIF
    # invece che ruotando i pixel; senza questo, "ritto" diventa "sdraiato")
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass  # non bloccante

    # Converti a RGB (le HEIC possono essere in altri color space, e il JPEG
    # in uscita ha bisogno di RGB)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Ridimensiona mantenendo le proporzioni
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > MAX_LONG_EDGE_PX:
        scale = MAX_LONG_EDGE_PX / long_edge
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)
        logger.info(
            "Scorecard image resized: %dx%d → %dx%d", w, h, new_size[0], new_size[1]
        )

    # Salva come JPEG con qualità alta ma non massima (il risparmio di
    # bytes è significativo, l'OCR non perde quasi nulla)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    jpeg_bytes = out.getvalue()

    if len(jpeg_bytes) > MAX_PAYLOAD_BYTES:
        # Riduci ancora qualità se necessario
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=72, optimize=True)
        jpeg_bytes = out.getvalue()
        logger.info(
            "Scorecard image quality reduced to fit payload limit: %d bytes",
            len(jpeg_bytes),
        )

    if len(jpeg_bytes) > MAX_PAYLOAD_BYTES:
        raise ScorecardImageError(
            f"Immagine troppo grande anche dopo compressione "
            f"({len(jpeg_bytes) // 1024} KB). Usa una foto a risoluzione più bassa."
        )

    return jpeg_bytes


def _call_anthropic_vision(jpeg_bytes: bytes, api_key: str) -> str:
    """
    Chiama l'API Anthropic Messages con la foto + il prompt strutturato.
    Ritorna il testo della risposta (che dovrebbe essere JSON puro).
    """
    try:
        # Import locale così l'app parte anche se anthropic non è installata
        # (la rotta /scorecard fallirà con errore esplicito, ma il resto di
        # NETGOLF continua a funzionare)
        from anthropic import Anthropic
    except ImportError as e:
        raise ScorecardOCRConfigError(
            "Pacchetto 'anthropic' non installato sul server. "
            "Aggiungi 'anthropic' al requirements.txt."
        ) from e

    model = os.environ.get("NETGOLF_OCR_MODEL", DEFAULT_MODEL)

    client = Anthropic(api_key=api_key)
    image_b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": PROMPT_OCR_SCORECARD,
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        # Anthropic SDK ha eccezioni proprie (RateLimitError, APIError,
        # APIConnectionError). Le catturiamo tutte come ScorecardOCRError
        # con messaggio leggibile.
        raise ScorecardOCRError(
            f"Errore chiamata API Anthropic: {type(e).__name__}: {e}"
        ) from e

    # La risposta ha content come lista di blocks; ne prendiamo il testo.
    if not response.content:
        raise ScorecardOCRError("Risposta Anthropic vuota.")

    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
    if not text_blocks:
        raise ScorecardOCRError("Risposta Anthropic non contiene testo.")

    return "".join(text_blocks).strip()


def _parse_anthropic_response(raw_text: str) -> dict[str, Any]:
    """
    Parsa la risposta del modello come JSON. Tollera il caso in cui il modello,
    nonostante le istruzioni del prompt, racchiuda il JSON in markdown fences.
    """
    text = raw_text.strip()

    # Tolleranza markdown fences (a volte il modello aggiunge ```json ... ```
    # nonostante le istruzioni — lo gestiamo)
    if text.startswith("```"):
        # Rimuovi prima e ultima riga di fence
        lines = text.split("\n")
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Log del raw per debug ma non lo esponiamo all'utente (può essere lungo)
        logger.error("Risposta Anthropic non parsabile come JSON: %s", e)
        logger.error("Raw response (primi 500 char): %s", text[:500])
        raise ScorecardOCRError(
            f"Il modello ha restituito una risposta non in formato JSON valido: {e}"
        )


def _validate_structure(parsed: dict[str, Any]) -> None:
    """
    Validazione MOLTO leggera: logga warning se mancano sezioni top-level
    o se l'array buche non ha 18 elementi. Non solleva eccezioni: la review
    UI mostrerà comunque quello che c'è.
    """
    expected_top = {"torneo", "giocatore", "campo", "handicap", "buche"}
    missing = expected_top - set(parsed.keys())
    if missing:
        logger.warning("Risposta OCR senza sezioni: %s", missing)

    buche = parsed.get("buche") or []
    if not isinstance(buche, list):
        logger.warning("Sezione 'buche' non è una lista")
    elif len(buche) != 18:
        logger.warning("Sezione 'buche' ha %d elementi invece di 18", len(buche))
