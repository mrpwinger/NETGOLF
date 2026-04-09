"""
Entry point WSGI per gunicorn (production).

Uso:
    gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120

In sviluppo locale è meglio usare `flask run`:
    export FLASK_APP=netgolf:create_app
    flask run --port 3000
"""

from netgolf import create_app

app = create_app(config_path="config.yaml")
