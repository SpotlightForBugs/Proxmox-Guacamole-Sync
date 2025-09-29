#!/usr/bin/env python3
"""
Example configuration file for Guacamole VM Manager

Copy this file to config.py and customize your settings.
The config.py file should be git-ignored to protect your credentials.

VM NOTES FORMAT EXAMPLES:

Basic multi-protocol examples:
  user:"admin" pass:"password123" protos:"rdp,vnc,ssh";

VNC-specific examples:
  user:"viewer" pass:"readonly" protos:"vnc" vnc_port:"5901" vnc_settings:"read-only=true,color-depth=16";
  user:"admin" pass:"admin123" protos:"vnc" vnc_settings:"encoding=tight,cursor=local,disable-copy=false";

Advanced VNC settings (vnc_settings parameter):
  - color-depth: 8, 16, 24, 32 (bit depth)
  - encoding: raw, rre, corre, hextile, zlib, tight, ultra
  - cursor: local, remote (cursor handling)
  - read-only: true/false (view-only mode)
  - disable-copy: true/false (disable clipboard copy)
  - disable-paste: true/false (disable clipboard paste)
  - enable-sftp: true/false (file transfer support)
  - swap-red-blue: true/false (color correction)
  - autoretry: number (connection retry attempts)

Template variables available:
  {vmname}, {user}, {proto}, {port}, {vmid}, {node}, {ip}, {hostname}
"""


class Config:
    """Configuration class for API endpoints and credentials"""

    # Guacamole Configuration
    GUAC_BASE_URL = "https://your-guacamole-server.example.com"
    GUAC_USERNAME = "your-guac-admin-username"
    GUAC_PASSWORD = "your-guac-admin-password"
    GUAC_DATA_SOURCE = "mysql"  # or "postgresql", "sqlserver"

    # Auto-discovered working API endpoints (populated automatically)
    # These are determined on first run and saved to avoid endpoint discovery
    GUAC_WORKING_BASE_PATH = None  # "/api" or "/guacamole/api"
    GUAC_WORKING_DATA_SOURCE = None  # "mysql", "postgresql", etc.

    # Proxmox Configuration
    PROXMOX_HOST = "192.168.1.100"  # Your Proxmox server IP
    PROXMOX_PORT = 8006
    # Format: user@realm!tokenid (e.g., "root@pam!mytoken")
    PROXMOX_TOKEN_ID = "root@pam!your-token-name"
    PROXMOX_SECRET = "your-proxmox-api-token-secret"

    # Default Connection Settings
    DEFAULT_RDP_PORT = 3389
    DEFAULT_VNC_PORT = 5900  # Standard VNC port; VNC uses ports 5900+display_number
    DEFAULT_SSH_PORT = 22

    # VNC Quality Settings (used as defaults for new connections)
    # Supported color depths: 8, 16, 24, 32 (higher = better quality)
    DEFAULT_VNC_COLOR_DEPTH = "32"
    # Supported encodings: raw, rre, corre, hextile, zlib, tight, ultra
    DEFAULT_VNC_ENCODING = "tight"  # Good balance of speed and compression

    # Encryption settings for password protection in VM notes
    # Generate a new key with: from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
    ENCRYPTION_KEY = "your_32_character_base64_encryption_key_here"

    @property
    def proxmox_base_url(self):
        return f"https://{self.PROXMOX_HOST}:{self.PROXMOX_PORT}/api2/json"

    @property
    def guac_connection_base(self):
        return (
            f"{self.GUAC_BASE_URL}/guacamole/api/session/data/{self.GUAC_DATA_SOURCE}"
        )
