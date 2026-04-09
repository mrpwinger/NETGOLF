"""
Due servizi crittografici separati, da NON confondere:

1. Password di login NETGOLF
   → hashata con Argon2id (`PasswordHasher`).
   → NON reversibile: verifichiamo e basta.

2. Password area riservata FIG
   → cifrata con AES-GCM usando la MASTER KEY del server.
   → reversibile: serve in chiaro per fare la POST di login su FederGolf.
   → la chiave sta in env var NETGOLF_MASTER_KEY (32 byte, base64-url).

La separazione è netta apposta: l'hash di Argon2id della password NETGOLF NON
viene mai usato come chiave di cifratura di nulla. Se volessimo passare alla
strada zero-knowledge (Strada A di cui abbiamo parlato), il punto di
intervento sarebbe `FigCredentialCipher`: deriveremmo la chiave dalla password
di login al posto della master key. La superficie di cambiamento è un solo
file.
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ─── Password login NETGOLF (Argon2id) ────────────────────────────────────────


class PasswordService:
    """Hash e verify della password di login NETGOLF."""

    def __init__(
        self,
        time_cost: int = 3,
        memory_cost: int = 65536,
        parallelism: int = 4,
        hash_len: int = 32,
        salt_len: int = 16,
    ):
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len,
        )

    def hash(self, password: str) -> str:
        """
        Restituisce la stringa Argon2id nel formato standard
        `$argon2id$v=19$m=...,t=...,p=...$salt$hash`. Da salvare tal quale
        nel campo `users.pwd_hash`.
        """
        if not password:
            raise ValueError("password vuota")
        return self._hasher.hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        """
        True se la password corrisponde all'hash, False altrimenti.
        Non rilancia eccezioni per mismatch (più comodo in auth flows).
        """
        try:
            return self._hasher.verify(stored_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        """
        True se i parametri Argon2 con cui l'hash fu generato sono più
        deboli di quelli attuali. Se True, l'app dovrebbe rigenerare
        l'hash al prossimo login riuscito.
        """
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return True


# ─── Password FIG (AES-GCM con master key del server) ───────────────────────


@dataclass
class CipherBlob:
    """
    Contenitore per dati cifrati, pronto per il salvataggio in DB.
    Tutti i campi sono base64-url (ASCII-safe, compatibile con SQLite).
    """

    ciphertext_b64: str  # include il tag di autenticazione GCM in coda
    nonce_b64: str       # 12 byte come raccomandato per AES-GCM

    def to_dict(self) -> dict[str, str]:
        return {"ciphertext": self.ciphertext_b64, "nonce": self.nonce_b64}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> CipherBlob:
        return cls(ciphertext_b64=d["ciphertext"], nonce_b64=d["nonce"])


class FigCredentialCipher:
    """
    Cifra/decifra la password dell'area riservata FIG con AES-GCM a 256 bit,
    usando la master key del server (Strada B).

    Note operative:
      - la master key deve essere esattamente 32 byte; accettiamo base64-url
        senza padding per comodità nelle env var;
      - AES-GCM richiede nonce *unici* per la stessa chiave: qui generiamo 12
        byte random per ogni cifratura (collision probability trascurabile con
        i nostri volumi: con 96 bit random si può arrivare a ~2^32 cifrature
        con probabilità collisione 2^-33);
      - come AAD (Additional Authenticated Data) passiamo lo user_id: così
        se qualcuno provasse a spostare il ciphertext di Alice nella riga di
        Bob, la decifratura fallirebbe. È una mitigazione cheap e utile.
    """

    def __init__(self, master_key: str):
        if not master_key:
            raise ValueError(
                "Master key FIG mancante. Impostare l'env var configurata "
                "in config.yaml (default: NETGOLF_MASTER_KEY)."
            )
        key_bytes = _decode_master_key(master_key)
        if len(key_bytes) != 32:
            raise ValueError(
                f"Master key deve essere 32 byte (decodificati), "
                f"trovati {len(key_bytes)}"
            )
        self._aesgcm = AESGCM(key_bytes)

    def encrypt(self, plaintext: str, user_id: int) -> CipherBlob:
        if plaintext is None:
            raise ValueError("plaintext None")
        nonce = os.urandom(12)
        aad = str(user_id).encode("utf-8")
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return CipherBlob(
            ciphertext_b64=_b64e(ct),
            nonce_b64=_b64e(nonce),
        )

    def decrypt(self, blob: CipherBlob, user_id: int) -> str:
        nonce = _b64d(blob.nonce_b64)
        ct = _b64d(blob.ciphertext_b64)
        aad = str(user_id).encode("utf-8")
        pt = self._aesgcm.decrypt(nonce, ct, aad)
        return pt.decode("utf-8")

    @staticmethod
    def generate_master_key() -> str:
        """
        Genera una nuova master key pronta per essere messa in env var.
        Usare solo una volta, all'installazione:
            python -c 'from netgolf.crypto import FigCredentialCipher; \\
                       print(FigCredentialCipher.generate_master_key())'
        """
        return _b64e(secrets.token_bytes(32))


# ─── Helpers base64-url ──────────────────────────────────────────────────────


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _decode_master_key(key_str: str) -> bytes:
    """
    Accetta la master key in vari formati di comodità:
      - base64-url (con o senza padding)
      - base64 standard
      - hex (64 char)
    """
    key_str = key_str.strip()
    # Hex?
    if len(key_str) == 64 and all(c in "0123456789abcdefABCDEF" for c in key_str):
        return bytes.fromhex(key_str)
    # base64 (url o standard)
    try:
        return _b64d(key_str)
    except Exception:
        pass
    try:
        return base64.b64decode(key_str + "=" * (-len(key_str) % 4))
    except Exception as e:
        raise ValueError(f"Master key non decodificabile: {e}") from e
