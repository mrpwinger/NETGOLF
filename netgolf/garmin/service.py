"""
Service layer per l'integrazione Garmin Connect.
Gestisce credenziali cifrate e import scorecard nel DB NETGOLF.
"""
from __future__ import annotations

import logging
from typing import Any

from ..crypto import CipherBlob, FigCredentialCipher

log = logging.getLogger(__name__)


class GarminCredentialsMissing(Exception):
    pass


class GarminService:

    def __init__(self, cipher: FigCredentialCipher):
        self.cipher = cipher

    @classmethod
    def from_app(cls) -> "GarminService":
        from flask import current_app
        cfg = current_app.config["NETGOLF"]
        cipher = FigCredentialCipher(cfg.master_key())
        return cls(cipher)

    def get_client(self, user):
        from .client import GarminClient
        from ..models import GarminCredential

        cred: GarminCredential | None = user.garmin_credential
        if not cred:
            raise GarminCredentialsMissing(
                "Credenziali Garmin non configurate. Aggiungile nel profilo."
            )
        blob = CipherBlob(
            ciphertext_b64=cred.password_ciphertext,
            nonce_b64=cred.password_nonce,
        )
        password = self.cipher.decrypt(blob, user.id)
        return GarminClient(cred.email, password)

    def save_credentials(self, user, email: str, password: str) -> None:
        from ..db import db
        from ..models import GarminCredential

        blob = self.cipher.encrypt(password, user.id)
        cred = user.garmin_credential
        if cred is None:
            cred = GarminCredential(user_id=user.id)
            db.session.add(cred)
        cred.email = email
        cred.password_ciphertext = blob.ciphertext_b64
        cred.password_nonce = blob.nonce_b64
        db.session.commit()

    def delete_credentials(self, user) -> None:
        from ..db import db
        cred = user.garmin_credential
        if cred:
            db.session.delete(cred)
            db.session.commit()

    def fetch_scorecards(self, user) -> list[dict]:
        """Scarica tutte le scorecard Garmin dell'utente."""
        client = self.get_client(user)
        return client.fetch_all_scorecards()

    def import_scorecards(self, user) -> dict[str, int]:
        """
        Scarica le scorecard Garmin e le importa nel DB come Scorecard NETGOLF.
        Salta le scorecard già importate (basandosi su data_gara + circolo + source='garmin').
        Ritorna {'importate': N, 'saltate': N, 'errori': N}.
        """
        from ..db import db
        from ..models import Scorecard, ScorecardHole
        from ..scorecard.storage import (
            colpi_ricevuti as calc_colpi,
            adjusted_gross_score,
            stableford_lordo,
            stableford_netto,
        )

        client = self.get_client(user)
        raw_scorecards = client.fetch_all_scorecards()

        importate = saltate = errori = 0

        for sc_data in raw_scorecards:
            try:
                data_gara = sc_data.get("data_gara")
                circolo = sc_data.get("circolo", "")
                garmin_id = sc_data.get("garmin_id")
                holes_data = sc_data.get("holes", [])

                if not data_gara or not holes_data:
                    saltate += 1
                    continue

                # Controlla duplicati: stessa data, circolo, source garmin
                existing = db.session.execute(
                    db.select(Scorecard).where(
                        Scorecard.user_id == user.id,
                        Scorecard.data_gara == data_gara,
                        Scorecard.circolo == circolo,
                        Scorecard.source == "garmin",
                    )
                ).scalar_one_or_none()

                if existing:
                    saltate += 1
                    continue

                # Calcola totali WHS buca per buca
                # hcp_gioco non disponibile da Garmin, usiamo handicapped_strokes
                # come stima indiretta — per ora lasciamo 0 colpi ricevuti
                stbl_lordo_tot = stbl_netto_tot = score_lordo_tot = ags_tot = 0
                holes_to_save = []

                for h in holes_data:
                    par = h.get("par")
                    score_raw = h.get("score_raw")
                    colpi = 0  # Garmin non fornisce hcp per buca
                    ags = adjusted_gross_score(par, score_raw, colpi)
                    stbl_l = stableford_lordo(par, ags)
                    stbl_n = stableford_netto(par, ags, colpi)

                    try:
                        score_int = int(score_raw)
                        score_lordo_tot += score_int
                    except (ValueError, TypeError):
                        pass

                    if ags is not None:
                        ags_tot += ags
                    stbl_lordo_tot += stbl_l
                    stbl_netto_tot += stbl_n

                    holes_to_save.append({
                        "buca": h.get("buca"),
                        "par": par,
                        "metri_uomini": None,
                        "ordine_colpi": None,
                        "score_raw": score_raw,
                        "score_ags": ags,
                        "colpi_ricevuti": colpi,
                        "stbl_lordo": stbl_l,
                        "stbl_netto": stbl_n,
                    })

                # Crea Scorecard
                sc = Scorecard(
                    user_id=user.id,
                    torneo_nome=sc_data.get("torneo_nome"),
                    data_gara=data_gara,
                    circolo=circolo,
                    source="garmin",
                    garmin_scorecard_id=str(garmin_id) if garmin_id else None,
                    stbl_lordo_totale=stbl_lordo_tot,
                    stbl_netto_totale=stbl_netto_tot,
                    score_lordo_totale=score_lordo_tot,
                    ags_totale=ags_tot,
                )
                db.session.add(sc)
                db.session.flush()

                for h in holes_to_save:
                    hole = ScorecardHole(scorecard_id=sc.id, **h)
                    db.session.add(hole)

                importate += 1

            except Exception as e:
                log.warning("Errore import scorecard Garmin %s: %s", sc_data.get("garmin_id"), e)
                errori += 1
                db.session.rollback()
                continue

        db.session.commit()
        log.info(
            "Import Garmin completato: %d importate, %d saltate, %d errori",
            importate, saltate, errori,
        )
        return {"importate": importate, "saltate": saltate, "errori": errori}
