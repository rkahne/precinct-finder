"""Local development server using waitress (Windows-compatible WSGI)."""
from waitress import serve
from app import app

print("Starting Precinct Finder on http://localhost:5000")
serve(app, host="0.0.0.0", port=5000)
