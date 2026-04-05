#!/bin/bash
# ============================================================
# Precinct Leader Finder — Digital Ocean Setup Script
# Run once as root on a fresh Ubuntu 22.04 droplet.
# ============================================================
set -e

APP_DIR="/opt/precinct-finder"
SERVICE_NAME="precinct-finder"
DB_NAME="precinctdb"
DB_USER="precinct_user"
# Generate a random password or set your own here:
DB_PASS="${PRECINCT_DB_PASS:-$(openssl rand -hex 20)}"

echo "==> Updating system packages..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx postgresql postgresql-contrib

echo "==> Creating app directory at $APP_DIR..."
mkdir -p "$APP_DIR"

# ── PostgreSQL setup ──────────────────────────────────────────────────────────

echo "==> Configuring PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

# Create DB user and database (idempotent)
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL

# Apply schema
sudo -u postgres psql -d "$DB_NAME" -f "$APP_DIR/db/init.sql"

DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_NAME}"
echo "    Database URL: $DATABASE_URL"
echo "    (Save this — you'll need it below)"

# ── Python virtualenv ─────────────────────────────────────────────────────────

echo "==> Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── systemd service ───────────────────────────────────────────────────────────

echo "==> Creating systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Precinct Leader Finder
After=network.target postgresql.service

[Service]
User=www-data
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/gunicorn -c gunicorn.conf.py "app:app"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Write .env file if it doesn't already exist
if [ ! -f "$APP_DIR/.env" ]; then
  cat > "$APP_DIR/.env" << EOF
DATABASE_URL=${DATABASE_URL}
# GOOGLE_CREDENTIALS_FILE=/opt/precinct-finder/google-credentials.json
# GOOGLE_SHEET_ID=your_google_sheet_id_here
PORT=5000
EOF
  echo "    Created $APP_DIR/.env — edit it to add Google Sheets credentials."
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

# ── nginx ─────────────────────────────────────────────────────────────────────

echo "==> Configuring nginx reverse proxy..."
cat > "/etc/nginx/sites-available/${SERVICE_NAME}" << 'EOF'
server {
    listen 80;
    server_name precinct-leaders.louisvilledems.com;

    proxy_buffer_size 128k;
    proxy_buffers 4 256k;

    # Serve static GeoJSON directly from disk (avoids Flask overhead)
    location /data/ {
        alias /opt/precinct-finder/static/data/;
        add_header Cache-Control "public, max-age=3600";
        gzip_static on;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 30;
    }
}
EOF

ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo ""
echo "==> Setup complete!"
echo "    App running at http://$(curl -s ifconfig.me)"
echo ""
echo "    DB password saved to: $APP_DIR/.env"
echo ""
echo "    To add Google Sheets export, edit $APP_DIR/.env and set:"
echo "      GOOGLE_CREDENTIALS_FILE=/opt/precinct-finder/google-credentials.json"
echo "      GOOGLE_SHEET_ID=<your spreadsheet id>"
echo "    Then: systemctl restart ${SERVICE_NAME}"
echo ""
echo "    To update leader counts from Excel:"
echo "      source $APP_DIR/.env"
echo "      DATABASE_URL=\$DATABASE_URL $APP_DIR/venv/bin/python scripts/process_data.py"
