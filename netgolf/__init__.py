"""
Factory dell'applicazione Flask.

Uso:
    from netgolf import create_app
    app = create_app()
    app.run()

O via Flask CLI:
    export FLASK_APP=netgolf:create_app
    flask run

La factory carica config.yaml + CSV (via config.load_config), inizializza DB,
sessione, Babel, auth, e monta i blueprint. Nessuna configurazione è
hard-coded qui dentro: qualunque valore viene letto da AppConfig.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from flask import Flask
from flask_login import LoginManager

from .config import AppConfig, load_config
from .crypto import FigCredentialCipher, PasswordService
from .db import db
from .i18n import init_babel

from .garmin import bp as garmin_bp
app.register_blueprint(garmin_bp)

def create_app(
    config_path: str | Path = "config.yaml",
    project_root: str | Path | None = None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ── Carica config.yaml + CSV + campi_slope_cr.json ────────────────────
    if project_root is None:
        project_root = Path(config_path).parent.resolve() if Path(config_path).is_absolute() else Path.cwd()
    gs_cfg: AppConfig = load_config(config_path=config_path, project_root=project_root)
    app.config["NETGOLF"] = gs_cfg

# ── Logging ───────────────────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, gs_cfg.raw.app.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # File di log dedicato agli eventi di accesso (login, logout, register,
    # fetch FIG, ecc.). Vive accanto al DB nel volume persistente, così
    # sopravvive ai redeploy. Rotation automatica a 5 MB, max 10 file
    # ruotati, totale 50 MB nel caso peggiore.
    try:
        from logging.handlers import RotatingFileHandler
        access_log_dir = Path(gs_cfg.database_url_absolute().replace("sqlite:////", "/").replace("sqlite:///", "")).parent
        access_log_dir.mkdir(parents=True, exist_ok=True)
        access_log_path = access_log_dir / "access.log"
        access_handler = RotatingFileHandler(
            access_log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        access_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        access_logger = logging.getLogger("netgolf.access")
        access_logger.setLevel(logging.INFO)
        access_logger.addHandler(access_handler)
        access_logger.propagate = False  # non duplica sul logger root
        app.logger.info("Access log file: %s", access_log_path)
    except Exception as e:
        app.logger.warning("Impossibile inizializzare access log file: %s", e)

    # ── Flask core settings (derivate da AppConfig) ──────────────────────
# ── Flask core settings (derivate da AppConfig) ──────────────────────
    app.config["SECRET_KEY"] = gs_cfg.secret_key()
    db_uri = gs_cfg.database_url_absolute()
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    # Assicura che la cartella del file SQLite esista (es. data/runtime/).
    # Importante quando il path del DB è dentro un volume montato a runtime:
    # Railway crea il mount point ma non sottocartelle annidate.
    if db_uri.startswith("sqlite:///"):
        from pathlib import Path as _P
        if db_uri.startswith("sqlite:////"):
            db_path = _P("/" + db_uri[len("sqlite:////"):])
        else:
            db_path = _P(db_uri[len("sqlite:///"):])
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            app.logger.info("DB dir verificata: %s", db_path.parent)
        except Exception as e:
            app.logger.warning("Impossibile creare la dir del DB %s: %s", db_path.parent, e)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        minutes=gs_cfg.raw.security.session.lifetime_minutes
    )
    app.config["SESSION_COOKIE_SECURE"] = gs_cfg.raw.security.session.cookie_secure
    app.config["SESSION_COOKIE_HTTPONLY"] = gs_cfg.raw.security.session.cookie_httponly
    app.config["SESSION_COOKIE_SAMESITE"] = gs_cfg.raw.security.session.cookie_samesite
    app.config["BABEL_DEFAULT_LOCALE"] = gs_cfg.raw.i18n.default_locale
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(
        (project_root / "translations").resolve()
    )

    # ── Servizi crittografici (istanze condivise) ────────────────────────
    a2 = gs_cfg.raw.security.argon2
    app.extensions["netgolf_password_service"] = PasswordService(
        time_cost=a2.time_cost,
        memory_cost=a2.memory_cost,
        parallelism=a2.parallelism,
        hash_len=a2.hash_len,
        salt_len=a2.salt_len,
    )

    master_key = gs_cfg.master_key()
    if master_key:
        app.extensions["netgolf_fig_cipher"] = FigCredentialCipher(master_key)
    else:
        # In dev: se la master key manca, l'app parte comunque ma il salvataggio
        # delle credenziali FIG fallirà con un 500 esplicito. Meglio così che
        # rifiutarsi di bootare in sviluppo.
        app.logger.warning(
            "Master key FIG non configurata (env var %s). "
            "Il salvataggio delle credenziali FIG non funzionerà.",
            gs_cfg.raw.security.master_key_env,
        )

    # ── Estensioni Flask ─────────────────────────────────────────────────
    db.init_app(app)
    init_babel(app)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.init_app(app)

    from .models import User  # noqa: WPS433 (import tardivo per evitare cicli)

    @login_manager.user_loader
    def _load_user(user_id: str):
        return db.session.get(User, int(user_id))

    # ── Blueprint ────────────────────────────────────────────────────────
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .campi import bp as campi_bp
    from .fig import bp as fig_bp
    from .frasi import bp as frasi_bp
    from .gesgolf import bp as gesgolf_bp
    from .main import bp as main_bp
    from .scorecard import bp as scorecard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(fig_bp)
    app.register_blueprint(gesgolf_bp)
    app.register_blueprint(campi_bp)
    app.register_blueprint(frasi_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(scorecard_bp)

    # ── CLI ──────────────────────────────────────────────────────────────
    from .cli import register_cli
    register_cli(app)

    # ── Auto-create tabelle in dev ───────────────────────────────────────
    if gs_cfg.raw.database.auto_create:
        with app.app_context():
            db.create_all()

    app.logger.info(
        "NETGOLF %s avviato | locale=%s | circoli=%d | frasi=%d",
        gs_cfg.raw.app.version,
        gs_cfg.raw.i18n.default_locale,
        len(gs_cfg.circoli),
        len(gs_cfg.frasi),
    )
    return app
