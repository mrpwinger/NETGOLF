from __future__ import annotations
import logging
from typing import Any

from ..crypto import FigCredentialCipher
from ..models import GarminCredential
from .client import GarminClient, GarminError, GarminLoginFailed, GarminRateLimited

log = logging.getLogger(__name__)


class GarminCredentialsMissing(Exception):
    pass


class GarminService:

    def __init__(self, cipher: FigCredentialCipher):
        self.cipher = cipher

    @classmethod
    def from_app(cls) -> "GarminService":
        from flask import current_app
        from ..crypto import FigCredentialCipher
        cfg = current_app.config["NETGOLF"]
        cipher = FigCredentialCipher(cfg.master_key())
        return cls(cipher)

    def get_client(self, user) -> GarminClient:
        cred: GarminCredential | None = user.garmin_credential
        if not cred:
            raise GarminCredentialsMissing(
                "Credenziali Garmin non configurate. Aggiungile nel profilo."
            )
        password = self.cipher.decrypt(cred.password_ciphertext, cred.password_nonce)
        return GarminClient(cred.email, password)

    def save_credentials(self, user, email: str, password: str) -> None:
        from ..db import db
        ciphertext, nonce = self.cipher.encrypt(password)
        cred = user.garmin_credential
        if cred is None:
            cred = GarminCredential(user_id=user.id)
            db.session.add(cred)
        cred.email = email
        cred.password_ciphertext = ciphertext
        cred.password_nonce = nonce
        db.session.commit()

    def delete_credentials(self, user) -> None:
        from ..db import db
        cred = user.garmin_credential
        if cred:
            db.session.delete(cred)
            db.session.commit()

    def fetch_scorecards(self, user) -> list[dict]:
        client = self.get_client(user)
        return client.fetch_all_scorecards()
