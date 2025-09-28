#!/bin/bash
# Setup script for Proxmox-Guacamole-Sync

echo "Setting up Proxmox-Guacamole-Sync..."

# Check if UV is installed
if ! command -v uv &> /dev/null; then
    echo "[pkg] Installing UV package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "✔ UV installed successfully"
else
    echo "✔ UV already installed"
fi

# Install dependencies
echo "[pkg] Installing Python dependencies..."
uv pip install -r requirements.txt

# Copy config if it doesn't exist
if [ ! -f config.py ]; then
    echo "[note] Creating config.py from template..."
    cp config_example.py config.py
    echo "! Please edit config.py with your Proxmox and Guacamole settings"
else
    echo "✔ config.py already exists"
fi

echo "[done] Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit config.py with your Proxmox and Guacamole settings"
echo "2. Test authentication: uv run python guac_vm_manager.py --test-auth"
echo "3. Add VMs: uv run python guac_vm_manager.py --add"