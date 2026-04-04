"""
Precinct Leader Finder — Flask backend

Environment variables:
    DATABASE_URL            PostgreSQL DSN
                            e.g. postgresql://user:pass@localhost/precinctdb
    GOOGLE_CREDENTIALS_FILE Path to Google service-account JSON file
    GOOGLE_SHEET_ID         Spreadsheet ID to append submissions to
    PORT                    Port to listen on (default: 5000)
"""
import os
import logging
import requests
from flask import Flask, jsonify, request, send_from_directory
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor

# Google Sheets support is optional — disabled gracefully if packages missing
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build as _google_build
    _GOOGLE_PACKAGES = True
except ImportError:
    _GOOGLE_PACKAGES = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", static_url_path="")

DATABASE_URL             = os.environ.get("DATABASE_URL", "")
GOOGLE_CREDENTIALS_FILE  = os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
GOOGLE_SHEET_ID          = os.environ.get("GOOGLE_SHEET_ID", "")
LEADER_THRESHOLD         = 3

# ---------------------------------------------------------------------------
# PostgreSQL connection pool
# ---------------------------------------------------------------------------

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set.")
        _pool = ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _pool


def _db():
    return _get_pool().getconn()


def _release(conn):
    _get_pool().putconn(conn)


# ---------------------------------------------------------------------------
# Google Sheets client
# ---------------------------------------------------------------------------

_sheets_svc = None


def _sheets():
    global _sheets_svc
    if _sheets_svc is not None:
        return _sheets_svc
    if not _GOOGLE_PACKAGES or not GOOGLE_CREDENTIALS_FILE or not GOOGLE_SHEET_ID:
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _sheets_svc = _google_build("sheets", "v4", credentials=creds)
        return _sheets_svc
    except Exception as exc:
        logger.error("Google Sheets init failed: %s", exc)
        return None


def _append_sheet(row):
    """Append one row to the configured Google Sheet. Returns True on success."""
    svc = _sheets()
    if svc is None:
        return False
    try:
        svc.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet1!A:K",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        return True
    except Exception as exc:
        logger.error("Sheets append failed: %s", exc)
        return False


def _client_ip():
    return (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or None
    )


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    _log_visit()
    return send_from_directory("static", "index.html")


def _log_visit():
    ip  = _client_ip()
    ua  = (request.headers.get("User-Agent", "") or "")[:500]
    ref = (request.headers.get("Referer", "") or "")[:500]
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO page_visits (ip_address, user_agent, referrer) VALUES (%s, %s, %s)",
                (ip, ua or None, ref or None),
            )
        conn.commit()
    except Exception as exc:
        logger.error("Failed to log visit: %s", exc)
        if conn:
            conn.rollback()
    finally:
        if conn:
            _release(conn)


# ---------------------------------------------------------------------------
# API: geocode an address via US Census Geocoder
# ---------------------------------------------------------------------------

@app.route("/api/geocode")
def geocode():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"error": "No address provided."}), 400

    query = address
    if "KY" not in address.upper() and "KENTUCKY" not in address.upper():
        query = f"{address}, Louisville, KY"

    try:
        resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": query, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        return jsonify({"error": "Geocoding service timed out. Please try again."}), 503
    except Exception as exc:
        return jsonify({"error": f"Geocoding request failed: {exc}"}), 500

    matches = data.get("result", {}).get("addressMatches", [])
    if not matches:
        return jsonify({
            "error": (
                'Address not found. Try including the city — '
                'e.g. "123 Main St, Louisville, KY".'
            )
        }), 404

    m = matches[0]
    return jsonify({
        "lat":             float(m["coordinates"]["y"]),
        "lon":             float(m["coordinates"]["x"]),
        "matched_address": m["matchedAddress"],
        "address_input":   address,
    })


# ---------------------------------------------------------------------------
# API: track a completed address + precinct search
# ---------------------------------------------------------------------------

@app.route("/api/track-search", methods=["POST"])
def track_search():
    data = request.get_json(silent=True) or {}
    ip   = _client_ip()
    ua   = (request.headers.get("User-Agent", "") or "")[:500]
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO searches
                   (address_input, matched_address, precinct_code, leg_dist, lat, lon, ip_address, user_agent)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    (data.get("address_input") or "")[:500] or None,
                    (data.get("matched_address") or "")[:500] or None,
                    (data.get("precinct_code") or "")[:20] or None,
                    (data.get("leg_dist") or "")[:20] or None,
                    data.get("lat"),
                    data.get("lon"),
                    ip,
                    ua or None,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.error("Failed to log search: %s", exc)
        if conn:
            conn.rollback()
    finally:
        if conn:
            _release(conn)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API: submit leader interest form
# ---------------------------------------------------------------------------

@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.get_json(silent=True) or {}

    first_name = (data.get("first_name") or "").strip()[:100]
    last_name  = (data.get("last_name")  or "").strip()[:100]
    email      = (data.get("email")      or "").strip()[:200]

    if not first_name or not last_name or not email:
        return jsonify({"error": "First name, last name, and email are required."}), 400

    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Please enter a valid email address."}), 400

    phone         = (data.get("phone")         or "").strip()[:50]  or None
    precinct_code = (data.get("precinct_code") or "").strip()[:20]  or None
    leg_dist      = (data.get("leg_dist")      or "").strip()[:20]  or None
    message       = (data.get("message")       or "").strip()[:2000] or None
    ip            = _client_ip()
    ua            = (request.headers.get("User-Agent", "") or "")[:500]

    conn          = None
    submission_id = None
    submitted_at  = None

    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO submissions
                   (first_name, last_name, email, phone, precinct_code, leg_dist,
                    message, ip_address, user_agent)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, submitted_at""",
                (first_name, last_name, email, phone,
                 precinct_code, leg_dist, message, ip, ua or None),
            )
            row          = cur.fetchone()
            submission_id = row[0]
            submitted_at  = row[1]
        conn.commit()
    except Exception as exc:
        logger.error("Failed to save submission: %s", exc)
        if conn:
            conn.rollback()
        return jsonify({"error": "Failed to save your submission. Please try again."}), 500
    finally:
        if conn:
            _release(conn)

    # Export to Google Sheets (best-effort, non-blocking)
    exported = _append_sheet([
        submission_id,
        submitted_at.isoformat() if submitted_at else "",
        first_name,
        last_name,
        email,
        phone or "",
        precinct_code or "",
        leg_dist or "",
        message or "",
        str(ip or ""),
    ])

    if exported:
        conn2 = None
        try:
            conn2 = _db()
            with conn2.cursor() as cur:
                cur.execute(
                    "UPDATE submissions SET exported_to_sheets = TRUE WHERE id = %s",
                    (submission_id,),
                )
            conn2.commit()
        except Exception:
            pass
        finally:
            if conn2:
                _release(conn2)

    return jsonify({"ok": True, "id": submission_id})


# ---------------------------------------------------------------------------
# API: live leader counts from database
# ---------------------------------------------------------------------------

@app.route("/api/leader-counts")
def leader_counts():
    conn = None
    try:
        conn = _db()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT precinct_code, unique_leaders, leg_dist FROM precincts"
            )
            rows = cur.fetchall()
        return jsonify({
            r["precinct_code"]: {
                "unique_leaders": r["unique_leaders"],
                "leg_dist":       r["leg_dist"],
            }
            for r in rows
        })
    except Exception as exc:
        logger.error("Failed to fetch leader counts: %s", exc)
        return jsonify({}), 500
    finally:
        if conn:
            _release(conn)


# ---------------------------------------------------------------------------
# API: summary stats
# ---------------------------------------------------------------------------

@app.route("/api/summary")
def summary():
    conn = None
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN unique_leaders >= %s THEN 1 ELSE 0 END) AS enough
                   FROM precincts""",
                (LEADER_THRESHOLD,),
            )
            row = cur.fetchone()
        total  = int(row[0] or 0)
        enough = int(row[1] or 0)
        return jsonify({
            "total_precincts":          total,
            "precincts_with_enough":    enough,
            "precincts_needing_leaders": total - enough,
            "leader_threshold":         LEADER_THRESHOLD,
        })
    except Exception as exc:
        logger.error("Failed to fetch summary: %s", exc)
        # Fall back to static file if DB is unavailable
        try:
            return send_from_directory("static/data", "summary.json")
        except Exception:
            return jsonify({}), 500
    finally:
        if conn:
            _release(conn)


# ---------------------------------------------------------------------------
# API: app configuration
# ---------------------------------------------------------------------------

@app.route("/api/config")
def config():
    return jsonify({"leader_threshold": LEADER_THRESHOLD})


# ---------------------------------------------------------------------------
# Entry point (development only — use gunicorn in production)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
