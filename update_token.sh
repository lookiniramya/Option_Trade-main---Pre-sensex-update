#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# update_token.sh — Update Paytm access token + restart collector
# ══════════════════════════════════════════════════════════════════════════════
# Run this each morning after logging into Paytm Money:
#   ./update_token.sh YOUR_ACCESS_TOKEN NIFTY 08-05-2026
#
# Or from your laptop via SSH:
#   ssh ubuntu@YOUR_ORACLE_IP './update_token.sh TOKEN NIFTY 08-05-2026'
# ══════════════════════════════════════════════════════════════════════════════

APP_DIR="/opt/optionsiq"
TOKEN="$1"
SYMBOL="${2:-NIFTY}"
EXPIRY="${3:-}"

if [ -z "$TOKEN" ]; then
    echo "Usage: ./update_token.sh ACCESS_TOKEN [SYMBOL] [EXPIRY_DD-MM-YYYY]"
    echo "Example: ./update_token.sh abc123xyz NIFTY 08-05-2026"
    exit 1
fi

echo "[*] Updating Paytm access token..."

# Update .env file
sed -i "s/^PAYTM_TOKEN=.*/PAYTM_TOKEN=$TOKEN/" "$APP_DIR/.env"
[ -n "$SYMBOL" ] && sed -i "s/^OPTIONSIQ_SYMBOL=.*/OPTIONSIQ_SYMBOL=$SYMBOL/" "$APP_DIR/.env"
[ -n "$EXPIRY" ] && sed -i "s/^OPTIONSIQ_EXPIRY=.*/OPTIONSIQ_EXPIRY=$EXPIRY/" "$APP_DIR/.env"

# Restart the collector with new token
echo "[*] Restarting data collector..."
sudo supervisorctl restart optionsiq-collector

# Wait and check
sleep 3
STATUS=$(sudo supervisorctl status optionsiq-collector | awk '{print $2}')
if [ "$STATUS" = "RUNNING" ]; then
    echo "[✓] Collector running with new token"
    echo "[✓] Symbol: $SYMBOL | Expiry: $EXPIRY"
    echo "[✓] Data collection active — app is live"
else
    echo "[✗] Collector failed to start. Check logs:"
    echo "    tail -f /var/log/optionsiq-collector.log"
fi
