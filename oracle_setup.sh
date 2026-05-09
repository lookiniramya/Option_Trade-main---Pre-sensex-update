#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# oracle_setup.sh — Complete OptionsIQ setup on Oracle Cloud Free Tier
# ══════════════════════════════════════════════════════════════════════════════
# Run this ONCE on your Oracle VM after SSH-ing in:
#   bash oracle_setup.sh
#
# This script:
#   1. Installs Python 3.11, git, nginx
#   2. Clones your GitHub repo
#   3. Installs all Python dependencies including pypmclient
#   4. Sets up systemd services so app + collector restart automatically
#   5. Configures nginx as reverse proxy with HTTPS
#   6. Opens firewall ports
# ══════════════════════════════════════════════════════════════════════════════

set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ── CONFIGURATION — edit before running ──────────────────────────────────────
GITHUB_REPO="https://github.com/lookiniramya/trade-helper-app"
APP_DIR="/opt/optionsiq"
APP_USER="optionsiq"
DOMAIN=""          # Leave blank to use IP address, or set to your domain
# ─────────────────────────────────────────────────────────────────────────────

echo "╔═══════════════════════════════════════════╗"
echo "║   OptionsIQ — Oracle Cloud Setup         ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

# ── Step 1: System updates ────────────────────────────────────────────────────
log "Updating system packages..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq
sudo apt-get install -y -qq \
    python3.11 python3.11-venv python3.11-dev \
    python3-pip git curl wget nginx \
    build-essential libssl-dev libffi-dev \
    supervisor sqlite3 ufw

# ── Step 2: Create app user ───────────────────────────────────────────────────
log "Creating app user: $APP_USER"
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd -m -s /bin/bash "$APP_USER"
fi

# ── Step 3: Clone repo ────────────────────────────────────────────────────────
log "Cloning repository..."
sudo mkdir -p "$APP_DIR"
sudo chown "$APP_USER:$APP_USER" "$APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
    warn "Repo already cloned, pulling latest..."
    sudo -u "$APP_USER" git -C "$APP_DIR" pull
else
    sudo -u "$APP_USER" git clone "$GITHUB_REPO" "$APP_DIR"
fi

# ── Step 4: Python virtual environment ───────────────────────────────────────
log "Creating Python virtual environment..."
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/venv"

log "Installing Python dependencies (including pypmclient from GitHub)..."
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install \
    -r "$APP_DIR/requirements.txt" -q

# ── Step 5: Environment file ──────────────────────────────────────────────────
log "Creating environment config..."
cat sudo tee "$APP_DIR/.env" > /dev/null << 'EOF'
# OptionsIQ Environment Variables
# Edit this file with your actual values after setup
ANTHROPIC_API_KEY=your_anthropic_api_key_here
OPTIONSIQ_APP_PASSWORD=your_app_password_here
PAYTM_TOKEN=                    # Set at runtime via collector
OPTIONSIQ_SYMBOL=NIFTY
OPTIONSIQ_EXPIRY=               # Set each week
EOF
sudo chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
sudo chmod 600 "$APP_DIR/.env"  # Only owner can read

# ── Step 6: Streamlit config ──────────────────────────────────────────────────
log "Configuring Streamlit..."
sudo -u "$APP_USER" mkdir -p "$APP_DIR/.streamlit"
cat sudo tee "$APP_DIR/.streamlit/config.toml" > /dev/null << 'EOF'
[server]
headless = true
port = 8501
address = "0.0.0.0"
enableCORS = false
enableXsrfProtection = false
fileWatcherType = "none"
maxUploadSize = 50

[browser]
gatherUsageStats = false
serverAddress = "0.0.0.0"

[runner]
installMissingPackages = false
EOF

# ── Step 7: Supervisor config (manages both services) ─────────────────────────
log "Setting up process supervisor..."
sudo tee /etc/supervisor/conf.d/optionsiq.conf > /dev/null << EOF
[program:optionsiq-app]
command=$APP_DIR/venv/bin/streamlit run $APP_DIR/app.py
directory=$APP_DIR
user=$APP_USER
autostart=true
autorestart=true
startretries=10
stopwaitsecs=30
stdout_logfile=/var/log/optionsiq-app.log
stderr_logfile=/var/log/optionsiq-app-err.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=3
environment=HOME="/home/$APP_USER",PATH="$APP_DIR/venv/bin:/usr/bin:/bin"

[program:optionsiq-collector]
command=$APP_DIR/venv/bin/python $APP_DIR/data_collector.py
directory=$APP_DIR
user=$APP_USER
autostart=true
autorestart=true
startretries=10
stopwaitsecs=10
stdout_logfile=/var/log/optionsiq-collector.log
stderr_logfile=/var/log/optionsiq-collector-err.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=3
environment=HOME="/home/$APP_USER",PATH="$APP_DIR/venv/bin:/usr/bin:/bin"

[group:optionsiq]
programs=optionsiq-app,optionsiq-collector
EOF

# ── Step 8: Nginx reverse proxy ───────────────────────────────────────────────
log "Configuring nginx..."
SERVER_NAME=${DOMAIN:-"_"}

sudo tee /etc/nginx/sites-available/optionsiq > /dev/null << EOF
server {
    listen 80;
    listen [::]:80;
    server_name $SERVER_NAME;

    # Streamlit needs these headers for WebSocket to work
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_buffering off;
    }

    # Health check endpoint
    location /_stcore/health {
        proxy_pass http://127.0.0.1:8501/_stcore/health;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/optionsiq /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

# ── Step 9: Firewall ──────────────────────────────────────────────────────────
log "Configuring firewall..."
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# Oracle Cloud also needs port rules in the cloud console
# (iptables rules that Oracle manages separately)
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 8501 -j ACCEPT
# Make iptables rules persist
sudo apt-get install -y -qq iptables-persistent
sudo netfilter-persistent save

# ── Step 10: Start services ───────────────────────────────────────────────────
log "Starting services..."
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start optionsiq:*

echo ""
echo "╔═══════════════════════════════════════════╗"
echo "║   Setup Complete!                         ║"
echo "╚═══════════════════════════════════════════╝"
echo ""
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "unknown")
echo "Your app URL: http://$PUBLIC_IP"
echo ""
echo "NEXT STEPS:"
echo "1. Edit secrets: nano $APP_DIR/.env"
echo "   → Add ANTHROPIC_API_KEY and OPTIONSIQ_APP_PASSWORD"
echo ""
echo "2. Check service status:"
echo "   sudo supervisorctl status"
echo ""
echo "3. View logs:"
echo "   tail -f /var/log/optionsiq-app.log"
echo "   tail -f /var/log/optionsiq-collector.log"
echo ""
echo "4. Update token daily (after Paytm login):"
echo "   $APP_DIR/update_token.sh YOUR_TOKEN NIFTY 08-05-2026"
echo ""
