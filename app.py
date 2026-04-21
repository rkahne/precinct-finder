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
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM     = os.environ.get("SMTP_FROM", SMTP_USER)
NOTIFY_EMAILS = [
    "chair@louisvilledems.com",
    "vicechair@louisvilledems.com",
    "communications@louisvilledems.com",
    "tech@louisvilledems.com",
    "jessicarhaggy89@gmail.com",
]

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


def _send_notification_email(submission):
    """Send a notification email to staff. Runs in a background thread."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        logger.info("SMTP not configured — skipping notification email.")
        return

    def _send():
        precinct    = submission.get("precinct_code") or "Unknown"
        leg_dist    = submission.get("leg_dist") or "Unknown"
        is_volunteer = submission.get("light_form", False)
        role_label  = "General Volunteer" if is_volunteer else "Precinct Leader Slot"

        legal_name = (
            f"{submission.get('legal_first_name', '')} "
            f"{submission.get('legal_middle_name') or ''} "
            f"{submission.get('legal_last_name', '')}".strip()
        )
        preferred = submission.get("preferred_first_name") or "—"
        email     = submission.get("email", "")
        phone     = submission.get("phone") or "Not provided"
        address   = (
            f"{submission.get('street_address', '')}, "
            f"{submission.get('city', '')}, "
            f"{submission.get('state', '')} "
            f"{submission.get('zip_code', '')}"
        ).strip(", ")
        birthdate   = submission.get("birthdate") or "Not provided"
        is_democrat = "Yes" if submission.get("is_democrat") else "No"

        if is_volunteer:
            body = f"""A new volunteer interest form was submitted.

Submission Type: {role_label}
Name:            {preferred} {submission.get('legal_last_name', '')}
Email:           {email}
Phone:           {phone}
Address:         {submission.get('street_address', '') or 'Not provided'}
Precinct:        {precinct}
Leg Dist:        {leg_dist}
"""
        else:
            body = f"""A new precinct leader interest form was submitted.

Submission Type: {role_label}
Legal Name:      {legal_name}
Preferred Name:  {preferred}
Email:           {email}
Phone:           {phone}
Address:         {address}
Birthdate:       {birthdate}
Democrat:        {is_democrat}
Precinct:        {precinct}
Leg Dist:        {leg_dist}
"""
        msg = MIMEMultipart()
        msg["From"]    = SMTP_FROM
        msg["To"]      = ", ".join(NOTIFY_EMAILS)
        msg["Subject"] = f"{role_label} Interest — Precinct {precinct}"
        msg.attach(MIMEText(body, "plain"))

        try:
            if SMTP_PORT == 465:
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    server.sendmail(SMTP_FROM, NOTIFY_EMAILS, msg.as_string())
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                    server.starttls()
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    server.sendmail(SMTP_FROM, NOTIFY_EMAILS, msg.as_string())
            logger.info("Notification email sent for precinct %s", precinct)
        except Exception as exc:
            logger.error("Failed to send notification email: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


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

def _geocode_census(query):
    """Try US Census geocoder. Returns (lat, lon, matched_address) or None."""
    try:
        resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": query, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        matches = resp.json().get("result", {}).get("addressMatches", [])
        if matches:
            m = matches[0]
            return float(m["coordinates"]["y"]), float(m["coordinates"]["x"]), m["matchedAddress"]
    except Exception as exc:
        logger.warning("Census geocoder failed: %s", exc)
    return None


def _geocode_arcgis(query):
    """Fallback to ArcGIS World Geocoder. Returns (lat, lon, matched_address) or None."""
    try:
        resp = requests.get(
            "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates",
            params={"SingleLine": query, "maxLocations": 1, "outFields": "Match_addr", "f": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        candidates = resp.json().get("candidates", [])
        if candidates and candidates[0].get("score", 0) >= 80:
            c = candidates[0]
            return float(c["location"]["y"]), float(c["location"]["x"]), c["attributes"].get("Match_addr", query)
    except Exception as exc:
        logger.warning("ArcGIS geocoder failed: %s", exc)
    return None


@app.route("/api/geocode")
def geocode():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"error": "No address provided."}), 400

    query = address
    if "KY" not in address.upper() and "KENTUCKY" not in address.upper():
        query = f"{address}, Louisville, KY"

    result = _geocode_census(query) or _geocode_arcgis(query)

    if not result:
        return jsonify({
            "error": (
                'Address not found. Try including the city — '
                'e.g. "123 Main St, Louisville, KY".'
            )
        }), 404

    lat, lon, matched_address = result
    return jsonify({
        "lat":             lat,
        "lon":             lon,
        "matched_address": matched_address,
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

    legal_first_name     = (data.get("legal_first_name")     or "").strip()[:100]
    legal_last_name      = (data.get("legal_last_name")      or "").strip()[:100]
    email                = (data.get("email")                or "").strip()[:200]
    phone                = (data.get("phone")                or "").strip()[:50]
    street_address       = (data.get("street_address")       or "").strip()[:200]
    city                 = (data.get("city")                 or "").strip()[:100]
    state                = (data.get("state")                or "").strip()[:50]
    zip_code             = (data.get("zip_code")             or "").strip()[:20]
    birthdate            = (data.get("birthdate")            or "").strip()[:20] or None
    preferred_first_name = (data.get("preferred_first_name") or "").strip()[:100] or None
    legal_middle_name    = (data.get("legal_middle_name")    or "").strip()[:100] or None
    precinct_code        = (data.get("precinct_code")        or "").strip()[:20]  or None
    leg_dist             = (data.get("leg_dist")             or "").strip()[:20]  or None
    is_democrat          = data.get("is_democrat")  # bool or None

    light_form = bool(data.get("light_form"))

    if light_form:
        if not legal_last_name or not email or not phone:
            return jsonify({"error": "Please fill in all required fields."}), 400
    else:
        if not legal_first_name or not legal_last_name or not email or not phone \
                or not street_address or not city or not state or not zip_code \
                or not birthdate or is_democrat is None:
            return jsonify({"error": "Please fill in all required fields."}), 400

    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Please enter a valid email address."}), 400

    ip = _client_ip()
    ua = (request.headers.get("User-Agent", "") or "")[:500]

    conn          = None
    submission_id = None
    submitted_at  = None

    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO submissions
                   (legal_first_name, preferred_first_name, legal_middle_name, legal_last_name,
                    street_address, city, state, zip_code,
                    email, phone, birthdate, is_democrat,
                    precinct_code, leg_dist, ip_address, user_agent)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, submitted_at""",
                (legal_first_name, preferred_first_name, legal_middle_name, legal_last_name,
                 street_address, city, state, zip_code,
                 email, phone or None, birthdate, is_democrat,
                 precinct_code, leg_dist, ip, ua or None),
            )
            row           = cur.fetchone()
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

    # Export to Google Sheets (best-effort)
    submission_type = "General Volunteer" if light_form else "Precinct Leader Slot"
    exported = _append_sheet([
        submission_id,
        submitted_at.isoformat() if submitted_at else "",
        submission_type,
        legal_first_name,
        preferred_first_name or "",
        legal_middle_name or "",
        legal_last_name,
        street_address,
        city,
        state,
        zip_code,
        email,
        phone or "",
        birthdate or "",
        ("Yes" if is_democrat else "No") if not light_form else "",
        precinct_code or "",
        leg_dist or "",
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

    # Notify staff (non-blocking)
    _send_notification_email({
        "legal_first_name":     legal_first_name,
        "preferred_first_name": preferred_first_name,
        "legal_last_name":      legal_last_name,
        "email":                email,
        "phone":                phone,
        "street_address":       street_address,
        "city":                 city,
        "state":                state,
        "zip_code":             zip_code,
        "birthdate":            birthdate,
        "is_democrat":          is_democrat,
        "precinct_code":        precinct_code,
        "leg_dist":             leg_dist,
        "light_form":           light_form,
    })

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
