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

    garmin_credential: Mapped["GarminCredential | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
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
class FigResult(db.Model):
    """
    Cache locale di una gara dello storico FIG di un utente. Popolata
    on-demand quando l'utente carica una scorecard che fa match (data + circolo)
    con una gara dello storico live, così le scorecard hanno una FK stabile
    a cui agganciarsi.
 
    Non è una sincronizzazione completa dello storico FIG: contiene solo le
    gare per cui esistono scorecard caricate. Le altre vivono solo in cache
    live durante il render del dashboard.
    """
 
    __tablename__ = "fig_results"
 
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
 
    # Identificativi della gara, normalizzati per il match
    data_gara: Mapped[str] = mapped_column(db.String(10), nullable=False, index=True)  # "YYYY-MM-DD"
    circolo: Mapped[str] = mapped_column(db.String(120), nullable=False)
    nome_torneo: Mapped[str | None] = mapped_column(db.String(200))
 
    # Snapshot dei dati FIG al momento del primo match (informativo)
    fig_data_raw: Mapped[str | None] = mapped_column(db.Text)  # JSON serialized
 
    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False)
 
    # Relazione one-to-many con le scorecard caricate per questa gara
    scorecards: Mapped[list["Scorecard"]] = relationship(
        back_populates="fig_result",
        cascade="all, delete-orphan",
    )
 
    __table_args__ = (
        db.UniqueConstraint("user_id", "data_gara", "circolo", name="uq_figresult_user_date_club"),
    )
 
 
class Scorecard(db.Model):
    """
    Scorecard caricata dall'utente: header con i dati di gara, percorso,
    handicap, e una relazione one-to-many con ScorecardHole per le 18 buche.
    """
 
    __tablename__ = "scorecards"
 
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
 
    # FK opzionale alla gara FIG. Se l'utente carica una scorecard di cui
    # NETGOLF non trova la gara nello storico, qui resta NULL.
    fig_result_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("fig_results.id", ondelete="SET NULL"), index=True
    )
 
    # Dati estratti dall'OCR e confermati dall'utente
    torneo_nome: Mapped[str | None] = mapped_column(db.String(200))
    data_gara: Mapped[str | None] = mapped_column(db.String(10), index=True)  # "YYYY-MM-DD"
    circolo: Mapped[str | None] = mapped_column(db.String(120), index=True)
    percorso: Mapped[str | None] = mapped_column(db.String(120))
    tee_colore: Mapped[str | None] = mapped_column(db.String(40))
    par_totale: Mapped[int | None] = mapped_column(db.Integer)
    cr: Mapped[float | None] = mapped_column(db.Float)
    sr: Mapped[int | None] = mapped_column(db.Integer)
 
    giocatore_nome: Mapped[str | None] = mapped_column(db.String(200))
    giocatore_tessera: Mapped[str | None] = mapped_column(db.String(20))
    hcp_index: Mapped[float | None] = mapped_column(db.Float)
    hcp_gioco: Mapped[int | None] = mapped_column(db.Integer)
 
    # Totali Stableford precalcolati (somma sulle 18 buche)
    stbl_lordo_totale: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    stbl_netto_totale: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    score_lordo_totale: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    ags_totale: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
 
    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False)
 
    # Relazioni
    fig_result: Mapped["FigResult | None"] = relationship(back_populates="scorecards")
    holes: Mapped[list["ScorecardHole"]] = relationship(
        back_populates="scorecard",
        cascade="all, delete-orphan",
        order_by="ScorecardHole.buca",
    )
 
 
class ScorecardHole(db.Model):
    """
    Una riga per ognuna delle 18 buche di una scorecard. Memorizza tutto
    quello che serve per visualizzare l'espansione nel dashboard senza
    ricalcoli al volo: par, ordine_colpi, score lordo, colpi tecnici
    ricevuti, Stableford lordo e netto.
    """
 
    __tablename__ = "scorecard_holes"
 
    id: Mapped[int] = mapped_column(primary_key=True)
    scorecard_id: Mapped[int] = mapped_column(
        db.ForeignKey("scorecards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    buca: Mapped[int] = mapped_column(db.Integer, nullable=False)  # 1..18
    par: Mapped[int | None] = mapped_column(db.Integer)
    metri_uomini: Mapped[int | None] = mapped_column(db.Integer)
    ordine_colpi: Mapped[int | None] = mapped_column(db.Integer)
 
    # Score: int normale (1..20) oppure stringa "X" per no-return.
    # Lo memorizziamo come stringa per gestire entrambi i casi.
    score_raw: Mapped[str | None] = mapped_column(db.String(4))
    score_ags: Mapped[int | None] = mapped_column(db.Integer)
 
    # Calcolati al momento del salvataggio (vedi netgolf/scorecard/stableford.py)
    colpi_ricevuti: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    stbl_lordo: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
    stbl_netto: Mapped[int] = mapped_column(db.Integer, default=0, nullable=False)
 
    scorecard: Mapped[Scorecard] = relationship(back_populates="holes")
 
    __table_args__ = (
        db.UniqueConstraint("scorecard_id", "buca", name="uq_scorecardhole_card_buca"),
    )

class GarminCredential(db.Model):
    """
    Credenziali Garmin Connect per un utente NETGOLF.
    One-to-one con User. Cifrata con AES-GCM come FigCredential.
    """
    __tablename__ = "garmin_credentials"

    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    email: Mapped[str] = mapped_column(db.String(200), nullable=False)
    password_ciphertext: Mapped[str] = mapped_column(db.Text, nullable=False)
    password_nonce: Mapped[str] = mapped_column(db.String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="garmin_credential")
