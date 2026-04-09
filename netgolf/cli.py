"""
Comandi CLI esposti via Flask CLI.

Esempi d'uso:
    flask gen-master-key     # genera una master key FIG
    flask campi-refresh      # rifà lo scraping del DB campi (TODO)
    flask create-admin EMAIL # promuove un utente esistente ad admin
"""

from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext
from sqlalchemy import select

from .crypto import FigCredentialCipher
from .db import db
from .models import User


def register_cli(app: Flask) -> None:
    app.cli.add_command(gen_master_key)
    app.cli.add_command(create_admin)
    app.cli.add_command(campi_refresh)


@click.command("gen-master-key")
def gen_master_key():
    """Genera una nuova master key FIG (da mettere in env var)."""
    key = FigCredentialCipher.generate_master_key()
    click.echo(f"NETGOLF_MASTER_KEY={key}")
    click.echo(
        "Aggiungi questa riga al tuo .env o alle variabili d'ambiente del servizio."
    )


@click.command("create-admin")
@click.argument("email")
@with_appcontext
def create_admin(email: str):
    """Promuove un utente esistente a admin."""
    user = db.session.scalar(select(User).where(User.email == email.lower()))
    if not user:
        click.echo(f"Utente {email} non trovato", err=True)
        return
    user.is_admin = True
    db.session.commit()
    click.echo(f"{email} ora è admin.")


@click.command("campi-refresh")
@click.option("--delay", default=0.2, help="Pausa tra richieste (secondi).")
@click.option("--timeout", default=12.0, help="Timeout per richiesta HTTP (secondi).")
@with_appcontext
def campi_refresh(delay: float, timeout: float):
    """
    Rifà lo scraping di FederGolf per aggiornare data/campi_slope_cr.json.
    Equivalente allo script Node scripts/scrape_campi.js del vecchio progetto.
    """
    from flask import current_app

    from .campi.scraper import CampiScraperError, scrape_campi

    gs_cfg = current_app.config["NETGOLF"]
    out_path = gs_cfg.project_root / gs_cfg.raw.data_files.campi_slope_cr

    click.echo(f"Scraping FederGolf -> {out_path}")
    click.echo("(può richiedere qualche minuto, ci sono ~220 circoli)")

    def progress(i, n, nome):
        click.echo(f"  [{i}/{n}] {nome[:30]}", nl=False)
        click.echo("\r", nl=False)

    try:
        db = scrape_campi(
            output_path=out_path,
            delay_sec=delay,
            timeout_sec=timeout,
            progress_callback=progress,
        )
    except CampiScraperError as e:
        click.echo(f"\nErrore scraping: {e}", err=True)
        return

    click.echo(f"\nFatto: {db['totale']} circoli salvati.")
    con_percorsi = sum(1 for c in db["circoli"] if c.get("percorsi"))
    click.echo(f"Di cui con percorsi popolati: {con_percorsi}")
    click.echo(
        "Nota: i circoli con 'percorsi: []' possono indicare un action/nonce "
        "WordPress cambiato. In quel caso aggiorna netgolf/campi/scraper.py."
    )
