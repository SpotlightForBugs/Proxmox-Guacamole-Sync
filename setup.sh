#!/bin/bash

# Guacamole VM Manager Setup Script
echo "🚀 Setting up Guacamole VM Manager..."

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3.7 or higher."
    exit 1
fi

echo "✓ Python 3 found: $(python3 --version)"

# Check if uv is available
if ! command -v uv &> /dev/null; then
    echo "Installing uv (fast Python package manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
    
    # Check again after installation
    if ! command -v uv &> /dev/null; then
        echo "✗ Failed to install uv. Falling back to pip..."
        if ! command -v pip3 &> /dev/null; then
            echo "Error: Neither uv nor pip3 is available. Please install one of them."
            exit 1
        fi
        pip3 install -r requirements.txt
    else
        echo "✓ uv installed successfully"
        uv sync
    fi
else
    echo "✓ uv found"
    # Install dependencies using uv
    echo "Installing Python dependencies with uv..."
    uv add requests urllib3
fi

if [ $? -eq 0 ]; then
    echo "✓ Dependencies installed successfully"
else
    echo "✗ Failed to install dependencies"
    exit 1
fi

# Check if config.py exists
echo ""
if [ ! -f "config.py" ]; then
    echo "📝 Creating configuration file..."
    cp config_example.py config.py
    echo "✓ Copied config_example.py to config.py"
    echo ""
    echo "⚠️  IMPORTANT: Please edit config.py with your credentials before running the script!"
    echo "   You need to configure:"
    echo "   - Guacamole server URL and credentials"
    echo "   - Proxmox server IP and API token"
    echo "   - Default VM username/password (currently set to johannes:johannes)"
    echo ""
else
    echo "✓ config.py already exists"
fi

# Make script executable
chmod +x guac_vm_manager.py
echo "✓ Made script executable"

# Test basic functionality
echo "Testing basic imports..."
uv run python3 -c "
import requests
import socket
import json
print('✓ All imports successful')
"

if [ $? -eq 0 ]; then
    echo "✓ Setup completed successfully!"
    echo ""
    echo "📋 Prerequisites checklist:"
    echo "   □ Edit config.py with your credentials"
    echo "   □ Ensure Proxmox API token has proper permissions"
    echo "   □ Verify privilege separation is DISABLED on Proxmox token"
    echo "   □ Install pve-dosthol on Proxmox server for Wake-on-LAN"
    echo ""
    echo "🧪 Test your setup:"
    echo "   uv run guac_vm_manager.py --test-auth"
    echo ""
    echo "🚀 Usage examples:"
    echo "   uv run guac_vm_manager.py              # Interactive menu"
    echo "   uv run guac_vm_manager.py --add        # Add VM connection"
    echo "   uv run guac_vm_manager.py --add -y     # Auto-approve mode"
    echo "   uv run guac_vm_manager.py --list       # List connections"
    echo ""
    echo "For detailed usage instructions, see README.md"
else
    echo "✗ Setup verification failed"
    exit 1
fi