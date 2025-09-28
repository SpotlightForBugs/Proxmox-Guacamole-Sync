#!/usr/bin/env python3
"""
Example configuration file for Guacamole VM Manager

Copy this file to config.py and customize your settings.
The config.py file should be git-ignored to protect your credentials.
"""

class Config:
    """Configuration class for API endpoints and credentials"""
    
    # Guacamole Configuration
    GUAC_BASE_URL = "https://your-guacamole-server.example.com"
    GUAC_USERNAME = "your-guac-admin-username"
    GUAC_PASSWORD = "your-guac-admin-password"
    GUAC_DATA_SOURCE = "mysql"  # or "postgresql", "sqlserver"
    
    # Proxmox Configuration
    PROXMOX_HOST = "192.168.1.100"  # Your Proxmox server IP
    PROXMOX_PORT = 8006
    # Format: user@realm!tokenid (e.g., "root@pam!mytoken")
    PROXMOX_TOKEN_ID = "root@pam!your-token-name"
    PROXMOX_SECRET = "your-proxmox-api-token-secret"
    
    # Default Connection Settings
    DEFAULT_RDP_PORT = 3389
    DEFAULT_VNC_PORT = 5900
    DEFAULT_SSH_PORT = 22
    
    # Encryption settings for password protection in VM notes
    # Generate a new key with: from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
    ENCRYPTION_KEY = "your_32_character_base64_encryption_key_here"
    
    @property
    def proxmox_base_url(self):
        return f"https://{self.PROXMOX_HOST}:{self.PROXMOX_PORT}/api2/json"

    @property
    def guac_connection_base(self):
        return f"{self.GUAC_BASE_URL}/guacamole/api/session/data/{self.GUAC_DATA_SOURCE}"