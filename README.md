# NETGOLF

Reingegnerizzazione dell'app GSCORE_V01-proxy da proxy Node.js a web app
Python/Flask, ribattezzata **NETGOLF** — il nome originale del front-end.

## Cosa è cambiato rispetto alla versione originale

La vecchia versione era un proxy Node.js che prendeva direttamente le
credenziali FederGolf dall'utente in ogni sessione e le usava per fare
scraping dell'area riservata. Nessun profilo utente, nessuna persistenza
delle credenziali, interfaccia single-page solo in italiano.

Questa versione:

- **Profilo utente NETGOLF dedicato** — ci si registra con email + password
  personale (Argon2id). Il login NETGOLF è separato dalle credenziali FIG.
- **Credenziali FIG salvate nel profilo** — tessera e password dell'area
  riservata si inseriscono una volta sola nel profilo utente. La password
  viene cifrata a riposo con AES-GCM (master key del server, vedi sotto).
- **Parametrica** — tutto il vecchio hard-coded è in `config.yaml` +
  CSV in `data/`. Cambiare URL/regex/timeout/circoli non richiede più
  ricompilazione.
- **i18n IT/EN** — italiano default, inglese via `?lang=en` + cookie.
- **SQLite locale** con schema versionato (SQLAlchemy).
- **Bug del vecchio `server.js` corretti** in passata: `ADMIN_TOKEN` mai
  dichiarato (ReferenceError a runtime), `resolveCircoloId` dichiarata due
  volte, regex `[^]*?` valida in JS ma non in Python, bug di
  case-sensitivity nella lookahead di `grabAfter` che troncava i nomi di
  circolo tipo "GOLF CLUB BERGAMO" a "GOLF".

## Struttura

```
NETGOLF/
├── config.yaml                     # Tutta la config parametrica
├── requirements.txt
├── wsgi.py                         # Entry point per gunicorn
├── Procfile                        # Comando deploy (Railway/Render)
├── build.sh                        # Build script per il deploy
├── .env.example                    # Template env var
│
├── data/                           # Anagrafiche
│   ├── circoli_gesgolf.csv
│   ├── hcp_bands.csv
│   ├── frasi_obiettivo.csv
│   └── campi_slope_cr.json
│
├── translations/                   # Flask-Babel
│   ├── it/LC_MESSAGES/messages.po
│   └── en/LC_MESSAGES/messages.po
│
└── netgolf/                        # Package Python
    ├── __init__.py                 # create_app() factory
    ├── config.py                   # loader YAML + CSV + pydantic
    ├── crypto.py                   # Argon2id + AES-GCM
    ├── db.py, models.py            # SQLAlchemy
    ├── i18n.py                     # locale selector
    ├── cli.py
    ├── auth/                       # registrazione, login, profilo
    ├── main/                       # landing + dashboard
    ├── fig/                        # client FederGolf
    ├── gesgolf/                    # client GesGolf
    ├── campi/                      # endpoint + scraper campi
    ├── frasi/                      # frasi obiettivo mensili
    ├── admin/                      # admin panel
    ├── static/
    └── templates/
```

## Setup (sviluppo locale)

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pybabel compile -d translations

cp .env.example .env
# Genera le chiavi e copiale in .env:
python -c 'from netgolf.crypto import FigCredentialCipher; print("NETGOLF_MASTER_KEY=" + FigCredentialCipher.generate_master_key())'
python -c 'import secrets; print("NETGOLF_SECRET_KEY=" + secrets.token_hex(32))'
python -c 'import secrets; print("NETGOLF_ADMIN_TOKEN=" + secrets.token_urlsafe(24))'

export FLASK_APP=netgolf:create_app
flask run --host 0.0.0.0 --port 3000
```

Al primo avvio la factory crea `data/netgolf.db`. Il primo utente che si
registra via `/auth/register` viene promosso admin automaticamente.

## Variabili d'ambiente

| Nome                    | Cosa è                                                       |
|-------------------------|--------------------------------------------------------------|
| `NETGOLF_SECRET_KEY`    | Chiave per firmare i cookie di sessione Flask (32+ byte)     |
| `NETGOLF_MASTER_KEY`    | Chiave 32 byte base64-url per cifrare le password FIG        |
| `NETGOLF_ADMIN_TOKEN`   | Token per endpoint admin via `?token=` (alt. a `is_admin`)   |

## Comandi CLI

```bash
flask gen-master-key                 # Genera una nuova master key
flask create-admin user@example.com  # Promuove un utente a admin
flask campi-refresh --delay 0.2      # Rifà lo scraping del DB campi
```

## Cifratura credenziali FIG

**Strada B**: master key del server, AES-GCM-256.

La password dell'area riservata FIG viene cifrata con `NETGOLF_MASTER_KEY`.
Il ciphertext e il nonce sono salvati in `fig_credentials`. Come AAD si
passa lo `user_id`: impedisce di spostare il ciphertext di un utente
nella riga di un altro.

- **DB rubato da solo** → ciphertext illeggibili.
- **Server intero compromesso** → master key nelle env var, attaccante può
  decifrare tutto.

Modello di minaccia: "database leak". Per "server compromise" serve passare
a Strada A (chiave derivata dalla password di login dell'utente, vedi
`netgolf/crypto.py`).

## Endpoint principali

| Metodo | Path                            | Descrizione                               |
|--------|---------------------------------|-------------------------------------------|
| GET    | `/`                             | Redirect a dashboard o login              |
| GET    | `/dashboard`                    | UI principale (login required)            |
| GET    | `/health`                       | Health check                              |
| GET    | `/api/config`                   | Config pubblica                           |
| GET/POST | `/auth/register`              | Registrazione                             |
| GET/POST | `/auth/login`                 | Login                                     |
| GET    | `/auth/logout`                  | Logout                                    |
| GET/POST | `/auth/profilo`               | Profilo + credenziali FIG                 |
| GET    | `/auth/lang/<lang>`             | Switch lingua                             |
| GET    | `/api/fig/profilo`              | Profilo FIG                               |
| GET    | `/api/fig/storico`              | Storico risultati                         |
| GET    | `/api/fig/all`                  | Profilo + storico in una chiamata         |
| GET    | `/api/gesgolf/score`            | Scorecard buca-per-buca                   |
| GET    | `/api/campi`                    | DB campi                                  |
| GET    | `/api/frase?hcp=12.3`           | Frase obiettivo del mese                  |
| GET    | `/admin`                        | Admin panel                               |

## Deploy in produzione

### Railway (con Volume persistente)

1. Push su GitHub.
2. Railway → New Project → Deploy from GitHub repo → seleziona NETGOLF.
3. Settings → Volumes → New Volume → mount path `/app/data`, 0.5 GB.
4. Variables: `NETGOLF_SECRET_KEY`, `NETGOLF_MASTER_KEY`,
   `NETGOLF_ADMIN_TOKEN`, `FLASK_APP=netgolf:create_app`,
   `PYTHON_VERSION=3.12`.
5. Build Command: `bash build.sh`
6. Start Command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
7. Networking → Generate Domain.

### Render (free, demo)

1. Push su GitHub.
2. Render → New → Web Service → connetti repo NETGOLF.
3. Build Command: `bash build.sh`
4. Start Command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
5. Environment Variables: come Railway, più `PYTHON_VERSION=3.12.3`.
6. Instance Type: Free.

**Limiti del free Render**: container in sleep dopo 15 minuti, filesystem
effimero (DB azzerato a ogni restart). Va bene per demo, non per uso reale.

## Cose note, non ancora fatte

- **Smoke test end-to-end**: i singoli moduli sono testati runtime, il
  boot completo no. Al primo avvio aspettati una piccola correzione.
- **`/api/debug-*`**: stub residui in dashboard.js, backend non li
  implementa. Il bottone "debug" darà 404.
- **Persistenza log su GitHub**: config prevista, codice non implementato.
- **Traduzioni EN**: ~80 stringhe tradotte a mano. Le altre ricadono
  sull'italiano (msgstr vuoto).

## Migrazione dati dal vecchio sistema

- `data/access_log.json` → richiede script di import per tabella `access_logs`
- `data/frasi_assegnate.json` → idem per `frasi_assegnate` (la chiave era
  `username_yyyy_m`, ora è `(user_id, anno, mese)`)
- `data/whitelist.json` → non migra (whitelist abolita)
- `data/campi_slope_cr.json` → copia diretta, formato identico
