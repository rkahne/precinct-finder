"""
Precinct Leader Finder — Flask backend
Serves the static frontend and proxies Census geocoding requests.

Environment variables:
    CONTACT_EMAIL   Email address for precinct leader interest submissions
                    (default: shown as placeholder — set this on your server)
    PORT            Port to listen on (default: 5000)
"""
import os
import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "YOUR_EMAIL_HERE@example.com")
LEADER_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ---------------------------------------------------------------------------
# API: geocode an address via US Census Geocoder
# ---------------------------------------------------------------------------

@app.route("/api/geocode")
def geocode():
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"error": "No address provided."}), 400

    # Default to Louisville, KY if no state hint present
    if "KY" not in address.upper() and "KENTUCKY" not in address.upper():
        query = f"{address}, Louisville, KY"
    else:
        query = address

    try:
        resp = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={
                "address": query,
                "benchmark": "Public_AR_Current",
                "format": "json",
            },
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
                "Address not found. Try including the city — "
                "e.g. \"123 Main St, Louisville, KY\"."
            )
        }), 404

    m = matches[0]
    return jsonify({
        "lat": float(m["coordinates"]["y"]),
        "lon": float(m["coordinates"]["x"]),
        "matched_address": m["matchedAddress"],
    })


# ---------------------------------------------------------------------------
# API: app configuration (exposes contact email to frontend)
# ---------------------------------------------------------------------------

@app.route("/api/config")
def config():
    return jsonify({
        "contact_email": CONTACT_EMAIL,
        "leader_threshold": LEADER_THRESHOLD,
    })


# ---------------------------------------------------------------------------
# Entry point (development only — use gunicorn in production)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
