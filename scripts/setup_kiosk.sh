#!/usr/bin/env bash
set -e

# ==============================================================================
# t-minus-pi Kiosk Setup Script for Raspberry Pi OS
# ==============================================================================

echo "Configuring Chromium kiosk autostart for t-minus-pi..."
echo "This script does not install or start the backend systemd service."

# Install required tools (Chromium, unclutter to hide mouse cursor, xdotool)
sudo apt update
sudo apt install -y chromium-browser unclutter xdotool sed

# Configure Wayland / X11 autostart
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat << 'EOF' > "$AUTOSTART_DIR/t-minus-pi-kiosk.desktop"
[Desktop Entry]
Type=Application
Name=t-minus-pi Dashboard
Exec=bash -c "sleep 5 && chromium-browser --noerrdialogs --disable-infobars --kiosk http://localhost:8000 --check-for-update-interval=31536000"
X-GNOME-Autostart-enabled=true
EOF

# Disable screen blanking / screen sleep
if grep -q "xserver-command=X -s 0 -dpms" /etc/lightdm/lightdm.conf 2>/dev/null; then
    echo "Screen blanking already disabled in lightdm.conf."
else
    echo "Disabling screen blanking in LightDM..."
    sudo sed -i 's/^#xserver-command=X/xserver-command=X -s 0 -dpms/' /etc/lightdm/lightdm.conf 2>/dev/null || true
fi

echo "Chromium kiosk autostart configured successfully."
