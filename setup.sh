#!/bin/bash
# ============================================================
# Precinct Leader Finder — Digital Ocean Setup Script
# Run once as root on a fresh Ubuntu 22.04 droplet.
# ============================================================
set -e

APP_DIR="/opt/precinct-finder"
APP_USER="precinct"
SERVICE_NAME="precinct-finder"

echo "==> Updating system packages..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx

echo "==> Creating app directory at $APP_DIR..."
mkdir -p "$APP_DIR"

# If running interactively, copy files from current directory
# (adjust source path as needed)
# cp -r . "$APP_DIR/"

echo "==> Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Creating systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Precinct Leader Finder
After=network.target

[Service]
User=www-data
WorkingDirectory=${APP_DIR}
Environment="CONTACT_EMAIL=YOUR_EMAIL_HERE@example.com"
ExecStart=${APP_DIR}/venv/bin/gunicorn -c gunicorn.conf.py "app:app"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo "==> Configuring nginx reverse proxy..."
cat > "/etc/nginx/sites-available/${SERVICE_NAME}" << 'EOF'
server {
    listen 80;
    server_name _;          # Replace _ with your domain if you have one

    # Increase buffer for large GeoJSON file
    proxy_buffer_size 128k;
    proxy_buffers 4 256k;

    # Serve the GeoJSON directly from disk (faster than Flask)
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
echo "    To update CONTACT_EMAIL, edit:"
echo "    /etc/systemd/system/${SERVICE_NAME}.service"
echo "    Then: systemctl daemon-reload && systemctl restart ${SERVICE_NAME}"
