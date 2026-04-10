"""
Modelli SQLAlchemy.

Note sulla cifratura:
  - users.pwd_hash contiene l'hash Argon2id della password di login NETGOLF.
    Non è reversibile: lo confrontiamo col PasswordService.verify().
  - fig_credentials.password_ciphertext contiene il ciphertext AES-GCM della
    password dell'area riservata FIG. Ciphertext e nonce stanno in colonne
    separate ma base64-url, e la chiave (master key del server) NON sta nel DB.
"""

from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(db.String(254), unique=True, nullable=False, index=True)
    pwd_hash: Mapped[str] = mapped_column(db.String(255), nullable=False)
    locale: Mapped[str] = mapped_column(db.String(5), default="it", nullable=False)
    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(db.DateTime)
    is_admin: Mapped[bool] = mapped_column(db.Boolean, default=False, nullable=False)

    # Relazione one-to-one con le credenziali FIG (opzionali)
    fig_credential: Mapped["FigCredential | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"

    @property
    def has_fig_credentials(self) -> bool:
        return self.fig_credential is not None


class FigCredential(db.Model):
    """
    Credenziali dell'area riservata FederGolf per un utente NETGOLF.

    Una sola riga per user_id (one-to-one). Se l'utente non ha ancora
    configurato le credenziali FIG, la riga non esiste proprio.
    """

    __tablename__ = "fig_credentials"

    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tessera: Mapped[str] = mapped_column(db.String(20), nullable=False)

    # Ciphertext AES-GCM della password FIG, base64-url.
    # Il tag GCM (16 byte) è concatenato al ciphertext dalla libreria cryptography.
    password_ciphertext: Mapped[str] = mapped_column(db.Text, nullable=False)
    password_nonce: Mapped[str] = mapped_column(db.String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    # Ultimo login FIG riuscito: se è None o scaduto, al prossimo uso si
    # ri-autentica automaticamente.
    last_fig_login_at: Mapped[datetime | None] = mapped_column(db.DateTime)

    user: Mapped[User] = relationship(back_populates="fig_credential")

    def __repr__(self) -> str:
        return f"<FigCredential user_id={self.user_id} tessera={self.tessera}>"


class AccessLog(db.Model):
    """
    Log degli accessi. Traslitterazione del `accessLog` in-memory del vecchio
    server.js, ma persistente nel DB invece che in JSON + GitHub.
    """

    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(db.ForeignKey("users.id", ondelete="SET NULL"))

    # Snapshot dell'email al momento del tentativo (utile anche se l'user
    # viene poi cancellato o se il tentativo era su un'email non registrata)
    email: Mapped[str | None] = mapped_column(db.String(254))

    # Dettagli del tentativo
    ip: Mapped[str | None] = mapped_column(db.String(45))
    user_agent: Mapped[str | None] = mapped_column(db.String(500))
    success: Mapped[bool] = mapped_column(db.Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(db.String(255))

    # Tipo di evento: "login_netgolf", "login_fig", "fig_fetch_profilo", ecc.
    event: Mapped[str] = mapped_column(db.String(40), nullable=False, default="login_netgolf")


class FraseAssegnata(db.Model):
    """
    Frase obiettivo del mese assegnata a un utente.
    Replica dell'oggetto `frasiAssegnate` di server.js, ma nel DB.
    """

    __tablename__ = "frasi_assegnate"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    anno: Mapped[int] = mapped_column(db.Integer, nullable=False)
    mese: Mapped[int] = mapped_column(db.Integer, nullable=False)
    frase_id: Mapped[str] = mapped_column(db.String(20), nullable=False)
    frase_testo: Mapped[str] = mapped_column(db.Text, nullable=False)
    fascia: Mapped[str] = mapped_column(db.String(40), nullable=False)
    assegnata_il: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "anno", "mese", name="uq_frase_user_periodo"),
    )
