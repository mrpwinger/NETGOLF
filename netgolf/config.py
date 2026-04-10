"""
Caricamento e validazione della configurazione.

Tutta la config statica vive in due posti:
  - config.yaml       → impostazioni, URL, selettori, timeout
  - data/*.csv        → anagrafiche (circoli, fasce HCP, frasi)

Questo modulo li legge, li valida con pydantic, e restituisce un oggetto
`AppConfig` che viene registrato in `app.config["NETGOLF"]` al boot.

Nessun altro modulo deve leggere config.yaml o i CSV direttamente: si passa
sempre da qui. Così se un domani cambiamo formato (es. TOML, DB, ...) tocchiamo
un punto solo.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


# ─── Modelli pydantic (specchio di config.yaml) ──────────────────────────────


class AppSection(BaseModel):
    name: str
    version: str
    release_date: str = ""
    whats_new: list[str] = []
    log_level: str = "INFO"
    debug_endpoints: bool = False


class ServerSection(BaseModel):
    host: str = "0.0.0.0"
    port: int = 3000
    secret_key_env: str = "NETGOLF_SECRET_KEY"
    secret_key_default: str = "dev-only-change-me-in-production"


class DatabaseSection(BaseModel):
    url: str
    auto_create: bool = True


class I18nSection(BaseModel):
    default_locale: str = "it"
    supported_locales: list[str] = ["it", "en"]
    cookie_name: str = "netgolf_lang"
    cookie_max_age_days: int = 365
    query_param: str = "lang"


class Argon2Params(BaseModel):
    time_cost: int = 3
    memory_cost: int = 65536
    parallelism: int = 4
    hash_len: int = 32
    salt_len: int = 16


class SessionParams(BaseModel):
    lifetime_minutes: int = 120
    cookie_secure: bool = False
    cookie_httponly: bool = True
    cookie_samesite: str = "Lax"


class SecuritySection(BaseModel):
    argon2: Argon2Params = Field(default_factory=Argon2Params)
    master_key_env: str = "NETGOLF_MASTER_KEY"
    session: SessionParams = Field(default_factory=SessionParams)


class FigLoginParams(BaseModel):
    user_field: str
    password_field: str
    csrf_field: str
    session_cookie: str


class FigStoricoParams(BaseModel):
    pagination_params: list[str]
    max_pages: int = 50
    retry_on_500: bool = True
    retry_delay_sec: int = 2
    rows_per_page: int = 100


class FigSection(BaseModel):
    base_url: str
    user_agent: str
    accept_language: str
    timeout_sec: int = 15
    endpoints: dict[str, str]
    login: FigLoginParams
    login_failed_markers: list[str]
    error_message_patterns: list[str]
    uuid_pattern: str
    profile_patterns: dict[str, str]
    profile_labels: list[str]
    storico: FigStoricoParams


class GesgolfSection(BaseModel):
    base_url: str
    user_agent: str
    timeout_sec: int = 10
    endpoints: dict[str, str]
    form_targets: dict[str, str]


class GithubPersistParams(BaseModel):
    enabled: bool = False
    repo_env: str = "GITHUB_REPO"
    token_env: str = "GITHUB_TOKEN"
    path: str = "data/access_log.json"
    debounce_sec: int = 5


class AccessLogSection(BaseModel):
    max_entries: int = 1000
    timezone: str = "Europe/Rome"
    github_persist: GithubPersistParams = Field(default_factory=GithubPersistParams)


class AdminSection(BaseModel):
    token_env: str = "NETGOLF_ADMIN_TOKEN"


class DataFilesSection(BaseModel):
    circoli_gesgolf: str
    hcp_bands: str
    frasi_obiettivo: str
    campi_slope_cr: str


class RawConfig(BaseModel):
    """Specchio diretto di config.yaml, validato."""

    app: AppSection
    server: ServerSection
    database: DatabaseSection
    i18n: I18nSection
    security: SecuritySection
    fig: FigSection
    gesgolf: GesgolfSection
    access_log: AccessLogSection
    admin: AdminSection
    data_files: DataFilesSection

    @field_validator("i18n")
    @classmethod
    def _default_in_supported(cls, v: I18nSection) -> I18nSection:
        if v.default_locale not in v.supported_locales:
            raise ValueError(
                f"default_locale '{v.default_locale}' non è in supported_locales "
                f"{v.supported_locales}"
            )
        return v


# ─── Modelli per i dati dei CSV ──────────────────────────────────────────────


class Circolo(BaseModel):
    nome_fig: str
    circolo_id: str
    aliases: list[str] = []


class HcpBand(BaseModel):
    min: float
    max: float
    label_it: str
    label_en: str
    bg: str
    accent: str

    def label(self, locale: str) -> str:
        return self.label_en if locale == "en" else self.label_it

    def contains(self, hcp: float) -> bool:
        return self.min <= hcp <= self.max


class FraseObiettivo(BaseModel):
    id: str
    fascia: str
    lang: str
    testo: str


# ─── Oggetto finale consumato dall'app ───────────────────────────────────────


class AppConfig:
    """
    Bundle di tutta la configurazione + anagrafiche, pronto per l'uso.

    Accessibile da `current_app.config["NETGOLF"]` in contesto Flask.
    """

    def __init__(
        self,
        raw: RawConfig,
        project_root: Path,
        circoli: list[Circolo],
        hcp_bands: list[HcpBand],
        frasi: list[FraseObiettivo],
        campi_slope_cr: dict[str, Any],
    ):
        self.raw = raw
        self.project_root = project_root
        self.circoli = circoli
        self.hcp_bands = hcp_bands
        self.frasi = frasi
        self.campi_slope_cr = campi_slope_cr

        # Indici pre-calcolati per lookup rapidi
        self._circoli_by_key: dict[str, str] = {}
        for c in circoli:
            self._circoli_by_key[c.nome_fig.upper()] = c.circolo_id
            for a in c.aliases:
                self._circoli_by_key[a.upper()] = c.circolo_id

    # ── comodità ─────────────────────────────────────────────────────────

    def secret_key(self) -> str:
        return os.environ.get(
            self.raw.server.secret_key_env, self.raw.server.secret_key_default
        )

    def master_key(self) -> str | None:
        """Master key per cifrare le password FIG. None se non configurata."""
        return os.environ.get(self.raw.security.master_key_env)

    def admin_token(self) -> str | None:
        return os.environ.get(self.raw.admin.token_env)

    def database_url_absolute(self) -> str:
        """
        Converte URL relativi tipo 'sqlite:///data/netgolf.db' in path assoluto
        basato sulla root del progetto. SQLAlchemy altrimenti lo risolve
        rispetto al CWD del processo, che non è affidabile.
        """
        url = self.raw.database.url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            rel = url.removeprefix("sqlite:///")
            abs_path = self.project_root / rel
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path}"
        return url

    # ── circoli ──────────────────────────────────────────────────────────

    def resolve_circolo_id(self, nome: str) -> str | None:
        """
        Risolve un nome di circolo (come appare su FederGolf) nel circolo_id
        GesGolf. Porta la logica di `resolveCircoloId()` di server.js:
          1. match esatto
          2. match su alias
          3. match parziale parola-per-parola
        """
        if not nome:
            return None
        key = nome.upper().strip()

        if key in self._circoli_by_key:
            return self._circoli_by_key[key]

        # Match parziale: una parola >3 char del nome cercato contenuta in
        # una chiave nota, o viceversa
        words = [w for w in key.split() if len(w) > 3]
        for w in words:
            for known_key, cid in self._circoli_by_key.items():
                if w in known_key or known_key.split()[0] in w:
                    return cid
        return None

    # ── HCP bands ────────────────────────────────────────────────────────

    def band_for_hcp(self, hcp: float) -> HcpBand | None:
        for b in self.hcp_bands:
            if b.contains(hcp):
                return b
        return None

    # ── frasi ────────────────────────────────────────────────────────────

    def frasi_per_fascia(self, fascia: str, lang: str = "it") -> list[FraseObiettivo]:
        return [f for f in self.frasi if f.fascia == fascia and f.lang == lang]


# ─── Loader ──────────────────────────────────────────────────────────────────


def _load_circoli(path: Path) -> list[Circolo]:
    out: list[Circolo] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aliases_raw = row.get("aliases", "") or ""
            aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()]
            out.append(
                Circolo(
                    nome_fig=row["nome_fig"].strip(),
                    circolo_id=row["circolo_id"].strip(),
                    aliases=aliases,
                )
            )
    return out


def _load_hcp_bands(path: Path) -> list[HcpBand]:
    out: list[HcpBand] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(
                HcpBand(
                    min=float(row["min"]),
                    max=float(row["max"]),
                    label_it=row["label_it"],
                    label_en=row["label_en"],
                    bg=row["bg"],
                    accent=row["accent"],
                )
            )
    # Ordiniamo dalla fascia più bassa (più brava) alla più alta
    out.sort(key=lambda b: b.min)
    return out


def _load_frasi(path: Path) -> list[FraseObiettivo]:
    out: list[FraseObiettivo] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(
                FraseObiettivo(
                    id=row["id"],
                    fascia=row["fascia"],
                    lang=row["lang"],
                    testo=row["testo"],
                )
            )
    return out


def _load_campi(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"aggiornato": None, "totale": 0, "circoli": []}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_config(
    config_path: str | Path = "config.yaml",
    project_root: str | Path | None = None,
) -> AppConfig:
    """
    Carica config.yaml + CSV e restituisce un AppConfig validato.

    Fallisce rumorosamente (pydantic ValidationError) se il YAML è malformato
    o mancano campi obbligatori. Questo è voluto: meglio crashare al boot che
    scoprire a runtime che la config è rotta.
    """
    config_path = Path(config_path)
    if project_root is None:
        project_root = config_path.parent.resolve()
    else:
        project_root = Path(project_root).resolve()

    with config_path.open(encoding="utf-8") as f:
        raw_dict = yaml.safe_load(f)

    raw = RawConfig(**raw_dict)

    def _abs(rel: str) -> Path:
        return project_root / rel

    circoli = _load_circoli(_abs(raw.data_files.circoli_gesgolf))
    hcp_bands = _load_hcp_bands(_abs(raw.data_files.hcp_bands))
    frasi = _load_frasi(_abs(raw.data_files.frasi_obiettivo))
    campi = _load_campi(_abs(raw.data_files.campi_slope_cr))

    return AppConfig(
        raw=raw,
        project_root=project_root,
        circoli=circoli,
        hcp_bands=hcp_bands,
        frasi=frasi,
        campi_slope_cr=campi,
    )
