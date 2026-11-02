"""Current platform entry point, without retired EDA/desktop-control routes.

Domain services own training, simulation, serial I/O and AI conversations. This
module only configures Flask, welcome navigation and the database route group.
Importing it never connects a serial port or starts MATLAB/training.
"""

import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from settings import configure

configure()

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from project_portal import DELTA_ROOT, register_project_portal, run_local_server
from csv_data_manager import CSVDataManager
from database_routes import register_database_routes


def local_secret():
    """Persist a random cookie secret locally; never ship a shared dev secret."""
    supplied = os.environ.get("WEBAPP_SECRET_KEY")
    if supplied:
        return supplied
    folder = Path(__file__).parent / "runtime"
    folder.mkdir(exist_ok=True)
    path = folder / "session.secret"
    try:
        # Exclusive creation keeps concurrent startup from replacing a secret.
        with path.open("x", encoding="ascii") as file:
            file.write(secrets.token_hex(32))
        if os.name != "nt":
            path.chmod(0o600)
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii").strip()


app = Flask(__name__)
app.secret_key = local_secret()
app.config.update(
    SEND_FILE_MAX_AGE_DEFAULT=0,
    MAX_CONTENT_LENGTH=210 * 1024**2,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
csv_data_manager = CSVDataManager()


@app.before_request
def local_api_boundary():
    """Device/file APIs belong to the local operator, not remote browsers.

    A public deployment needs its own authentication and device-agent design;
    binding to another interface must not expose local tools accidentally.
    """
    if not request.path.startswith("/api/"):
        return
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="本机工具接口仅支持本机访问。"), 403
    origin = request.headers.get("Origin")
    try:
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(error="不允许跨站访问本机工具。"), 403
    except ValueError:
        return jsonify(error="请求来源无效。"), 403


@app.after_request
def response_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/welcome")
@app.get("/role")
def role_select():
    return render_template("role.html")


@app.post("/enter")
@app.post("/role/set")
def role_set():
    session["platform_entered"] = True
    return redirect(url_for("index"))


@app.post("/api/role/set")
def api_role_set():
    session["platform_entered"] = True
    return jsonify(ok=True)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("platform_entered", None)
    return redirect(url_for("role_select"))


@app.get("/")
def index():
    return render_template(
        "main.html" if session.get("platform_entered") else "role.html"
    )


@app.get("/health")
def health():
    return jsonify(
        status="ok", project="双通道互济型电子电容", data_available=DELTA_ROOT.is_dir()
    )


@app.get("/run")
def run_page():
    """Keep old bookmarks working; project_portal installs the redirect."""
    return redirect(url_for("index"))


register_database_routes(app, csv_data_manager)
register_project_portal(app)

if __name__ == "__main__":
    run_local_server(app)
