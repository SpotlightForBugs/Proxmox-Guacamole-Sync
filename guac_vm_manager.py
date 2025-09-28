#!/usr/bin/env python3
"""
Guacamole VM Manager

A script to add VMs to Guacamole and manage Wake-on-LAN functionality.
Integrates with Guacamole and Proxmox APIs for seamless VM management.

Author: Johannes
Date: September 27, 2025
"""

import requests
import os
import socket
import struct
import json
import urllib3
from urllib.parse import urljoin
import getpass
import base64
import hashlib
from cryptography.fernet import Fernet
from typing import Dict, List, Optional, Tuple
import time
import sys
import subprocess
import re
import ipaddress

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn

try:
    from config import Config
except ImportError:
    print("Error: config.py not found!")
    print(" Please copy config_example.py to config.py and customize your settings.")
    print("   cp config_example.py config.py")
    sys.exit(1)

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initialize Rich console and Typer app
console = Console()
app = typer.Typer(
    name="guac-vm-manager",
    help="● Guacamole VM Manager - Sync Proxmox VMs with Apache Guacamole\n\n"
         "Automatically creates remote desktop connections (RDP/VNC/SSH) in Apache Guacamole\n"
         "by parsing VM credentials from Proxmox VM notes. Features IPv4-only networking,\n"
         "interactive connection management, and Wake-on-LAN support.",
    rich_markup_mode="rich",
    add_completion=False
)

ONBOARD_SENTINEL = os.path.expanduser("~/.guac_vm_manager_onboarded")

class AnimationManager:
    """Lightweight frame-based terminal animations (auto-disabled in non-TTY/tests).

    Usage:
        with AnimationManager("Authenticating", style="green") as anim:
            anim.update("Phase 2...")
    """
    FRAMES_SIMPLE = ["-","\\","|","/"]
    FRAMES_DOTS = ["∙  ","∙∙ ","∙∙∙"," ∙∙","  ∙"]
    FRAMES_BRAILLE = ["⣾","⣷","⣯","⣟","⡿","⢿","⣻","⣽"]

    def __init__(self, title: str, style: str = "cyan", frames: Optional[List[str]] = None, interval: float = 0.08):
        self.title = title
        self.style = style
        self.frames = frames or self.FRAMES_BRAILLE
        self.interval = interval
        self._stop = False
        self._thread = None  # set in __enter__
        self.enabled = sys.stdout.isatty() and not os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("GUAC_DISABLE_ANIM")
        self.current_msg = title

    def update(self, msg: str):
        self.current_msg = msg

    def __enter__(self):
        if not self.enabled:
            return self
        import threading
        def run():
            idx = 0
            while not self._stop:
                frame = self.frames[idx % len(self.frames)]
                console.print(f"[bold {self.style}]{frame}[/bold {self.style}] {self.current_msg}    ", end="\r")
                time.sleep(self.interval)
                idx += 1
            # Clear line
            console.print(" " * 80, end="\r")
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.enabled:
            return False
        self._stop = True
        if self._thread:
            self._thread.join(timeout=0.5)
        # Final line
        status = "DONE" if exc is None else "ERROR"
        console.print(f"[bold {('green' if exc is None else 'red')}]{status}[/bold {('green' if exc is None else 'red')}] {self.title}")
        return False

def run_onboarding():
    """First-time onboarding flow (or invoked by --onboarding).

    Adds validation of the encryption key so users immediately know if
    password-at-rest protection will function. If the key is invalid,
    offers an interactive regeneration (when TTY).
    """
    console.print(Panel.fit(" Guacamole VM Manager Onboarding ", border_style="cyan"))
    steps = [
        "Checking environment",
        "Validating config.py",
        "Validating encryption key",
        "Testing Guacamole authentication",
        "Testing Proxmox authentication",
        "Explaining VM notes format",
        "Next steps"
    ]
    from rich.progress import Progress, SpinnerColumn, TextColumn
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Starting onboarding...", total=len(steps))
        guac_auth_ok = prox_auth_ok = enc_ok = False
        for s in steps:
            progress.update(task, description=s)
            # Perform actual logic per step
            if s == "Checking environment":
                time.sleep(0.1)
            elif s == "Validating config.py":
                # Basic presence checks
                missing = []
                try:
                    from config import Config as _Cfg  # local import
                    required = ["GUAC_BASE_URL", "GUAC_USERNAME", "GUAC_PASSWORD", "PROXMOX_HOST", "PROXMOX_TOKEN_ID", "PROXMOX_SECRET"]
                    for attr in required:
                        if not getattr(_Cfg, attr, None):
                            missing.append(attr)
                    if missing:
                        console.print(f"[red]Missing config attributes: {', '.join(missing)}[/red]")
                    else:
                        console.print("[green]config.py basic values present[/green]")
                except Exception as e:
                    console.print(f"[red]Failed to import config: {e}[/red]")
            elif s == "Validating encryption key":
                try:
                    from config import Config as _Cfg  # re-import safe
                    key = getattr(_Cfg, 'ENCRYPTION_KEY', None)
                    if not key:
                        console.print("[red]ENCRYPTION_KEY missing in config.py[/red]")
                    else:
                        try:
                            from cryptography.fernet import Fernet
                            f = Fernet(key)
                            test_plain = b"verification-test"
                            token = f.encrypt(test_plain)
                            if f.decrypt(token) == test_plain:
                                console.print("[green]Encryption key is valid (encrypt/decrypt successful)[/green]")
                                console.print("Note: Any plain passwords in VM notes will be auto-migrated to encrypted form.")
                                enc_ok = True
                            else:
                                console.print("[red]Encryption key round-trip failed[/red]")
                        except Exception as e:
                            console.print(f"[red]Invalid ENCRYPTION_KEY: {e}[/red]")
                            # Offer regeneration if interactive
                            if sys.stdin.isatty():
                                resp = input("Generate and patch a new Fernet key into config.py now? (y/N): ").strip().lower()
                                if resp in ("y", "yes"):
                                    try:
                                        from cryptography.fernet import Fernet
                                        new_key = Fernet.generate_key().decode()
                                        # Patch config.py line in-place
                                        cfg_path = os.path.join(os.path.dirname(__file__), 'config.py')
                                        try:
                                            with open(cfg_path, 'r', encoding='utf-8') as cf:
                                                content = cf.readlines()
                                            for i, line in enumerate(content):
                                                if line.strip().startswith('ENCRYPTION_KEY') or 'ENCRYPTION_KEY =' in line:
                                                    # Preserve indentation
                                                    indent = line[:len(line) - len(line.lstrip())]
                                                    content[i] = f"{indent}ENCRYPTION_KEY = \"{new_key}\"\n"
                                                    break
                                            with open(cfg_path, 'w', encoding='utf-8') as cf:
                                                cf.writelines(content)
                                            console.print("[green]Generated and wrote new ENCRYPTION_KEY to config.py[/green]")
                                            enc_ok = True
                                        except Exception as werr:
                                            console.print(f"[red]Failed to write new key: {werr}[/red]")
                                    except Exception as gerr:
                                        console.print(f"[red]Could not generate key: {gerr}[/red]")
                            else:
                                console.print("Run interactively to auto-generate a new Fernet key, or manually run: from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
                except Exception as e:
                    console.print(f"[red]Encryption key validation error: {e}[/red]")
            elif s == "Testing Guacamole authentication":
                try:
                    from config import Config as _Cfg
                    cfg_obj = _Cfg()
                    ga = GuacamoleAPI(cfg_obj)
                    if ga.authenticate():
                        console.print("[green]Guacamole auth OK[/green]")
                        guac_auth_ok = True
                    else:
                        console.print("[red]Guacamole auth failed[/red]")
                except Exception as e:
                    console.print(f"[red]Guacamole auth error: {e}[/red]")
            elif s == "Testing Proxmox authentication":
                try:
                    from config import Config as _Cfg
                    cfg_obj = _Cfg()
                    pa = ProxmoxAPI(cfg_obj)
                    if pa.test_auth():
                        prox_auth_ok = True
                    else:
                        console.print("[red]Proxmox auth failed[/red]")
                except Exception as e:
                    console.print(f"[red]Proxmox auth error: {e}[/red]")
            elif s == "Explaining VM notes format":
                console.print("\nStructured credential line examples:")
                console.print('  user:"admin" pass:"P@ss" protos:"rdp,vnc,ssh" rdp_port:"3390" vnc_port:"5901" confName:"{vmname}-{user}-{proto}";')
                console.print('  user:"viewer" pass:"view123" protos:"vnc" vnc_settings:"color-depth=16,encoding=raw,read-only=true";')
                console.print("Lines end with semicolons; unrecognized free-form lines are preserved but ignored for parsing.")
            elif s == "Next steps":
                console.print("\nNext steps:")
                console.print("  • Add structured lines to VM notes (or let auto-migration encrypt existing ones).")
                console.print("  • Run 'auto' mode to create/update connections.")
                console.print("  • Use the sync option to pull settings back from Guacamole if needed.")
                # Summarize statuses
                console.print("\nStatus summary:")
                console.print(f"  Encryption key: {'OK' if enc_ok else 'ISSUE'}")
                console.print(f"  Guacamole auth: {'OK' if guac_auth_ok else 'ISSUE'}")
                console.print(f"  Proxmox API: {'OK' if prox_auth_ok else 'ISSUE'}")
            progress.advance(task)

    console.print("\n[bold green]Onboarding complete.[/bold green]")
    console.print("A quick start:")
    console.print("  1. Put credential lines in Proxmox VM notes.")
    console.print("  2. Run: uv run python guac_vm_manager.py (interactive).")
    console.print("  3. Choose option 2 to auto-add all configured VMs.")
    try:
        with open(ONBOARD_SENTINEL, 'w') as f:
            f.write(str(int(time.time())))
    except Exception:
        pass

class GuacamoleAPI:
    """Handles Guacamole API interactions"""
    
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.verify = False  # For self-signed certificates
        self.auth_token = None
        preferred_sources = [config.GUAC_DATA_SOURCE, "mysql", "postgresql", "sqlserver"]
        # Preserve order while removing duplicates
        self.data_sources = []
        for source in preferred_sources:
            if source and source not in self.data_sources:
                self.data_sources.append(source)

        self.api_base_paths = []
        for data_source in self.data_sources:
            self.api_base_paths.append(f"/guacamole/api/session/data/{data_source}")
            self.api_base_paths.append(f"/api/session/data/{data_source}")
        
    def authenticate(self, silent: bool = False) -> bool:
        """Authenticate with Guacamole and get auth token"""
        # Try different possible endpoint paths
        # /guacamole/api/tokens is for installations in subdirectories
        # /api/tokens is for root installations or reverse proxy setups
        endpoints = [
            "/guacamole/api/tokens",
            "/api/tokens"
        ]
        
        auth_data = {
            'username': self.config.GUAC_USERNAME,
            'password': self.config.GUAC_PASSWORD
        }
        
        # Animated authentication (skip if silent)
        from rich.panel import Panel
        
        if silent:
            # Silent authentication for test-auth command
            for endpoint in endpoints:
                auth_url = urljoin(self.config.GUAC_BASE_URL, endpoint)
                try:
                    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                    response = self.session.post(auth_url, data=auth_data, headers=headers)
                    if response.status_code == 200:
                        auth_response = response.json()
                        self.auth_token = auth_response.get('authToken')
                        if self.auth_token:
                            # Use Guacamole-Token header instead of query parameter (newer versions)
                            self.session.headers.update({'Guacamole-Token': self.auth_token})
                            return True
                        else:
                            # Silent failure, try next endpoint
                            continue
                    elif response.status_code == 404:
                        # Expected for installations without /guacamole prefix
                        continue
                    else:
                        # Silent failure, try next endpoint
                        continue
                        
                except requests.exceptions.RequestException:
                    # Silent failure, try next endpoint
                    continue
        else:
            # Normal authentication with animation
            with AnimationManager("Authenticating with Guacamole"):
                for endpoint in endpoints:
                    auth_url = urljoin(self.config.GUAC_BASE_URL, endpoint)
                    try:
                        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                        response = self.session.post(auth_url, data=auth_data, headers=headers)
                        if response.status_code == 200:
                            auth_response = response.json()
                            self.auth_token = auth_response.get('authToken')
                            if self.auth_token:
                                # Use Guacamole-Token header instead of query parameter (newer versions)
                                self.session.headers.update({'Guacamole-Token': self.auth_token})
                                console.print(Panel(" Authentication successful!", border_style="green"))
                                return True
                            else:
                                # Silent failure, try next endpoint
                                continue
                        elif response.status_code == 404:
                            # Expected for installations without /guacamole prefix
                            continue
                        else:
                            # Silent failure, try next endpoint
                            continue
                            
                    except requests.exceptions.RequestException:
                        # Silent failure, try next endpoint
                        continue
        
        if not silent:
            console.print(Panel(" Authentication failed - check credentials and server configuration", border_style="red"))
        return False

    def _build_api_endpoints(self, resource: str) -> List[str]:
        return [
            urljoin(self.config.GUAC_BASE_URL, f"{base}/{resource}")
            for base in self.api_base_paths
        ]
    
    def get_connections(self) -> Dict:
        """Get list of existing connections"""
        if not self.auth_token:
            if not self.authenticate():
                return {}
        
        for connections_url in self._build_api_endpoints("connections"):
            try:
                response = self.session.get(connections_url)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to get connections from {connections_url}: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Request failed for {connections_url}: {e}")
                continue
        
        print("Failed to get connections from all endpoints")
        return {}

    def get_connection_details(self, connection_id: str) -> Dict:
        """Get detailed connection parameters for a specific connection"""
        if not self.auth_token:
            if not self.authenticate():
                return {}
        
        # Try different API endpoint paths and data sources
        for api_base in self.api_base_paths:
            try:
                # First try to get connection details
                detail_url = f"{self.config.GUAC_BASE_URL}{api_base}/connections/{connection_id}"
                response = self.session.get(detail_url)
                
                if response.status_code == 200:
                    connection_info = response.json()
                    
                    # Now try to get connection parameters
                    params_url = f"{self.config.GUAC_BASE_URL}{api_base}/connections/{connection_id}/parameters"
                    params_response = self.session.get(params_url)
                    
                    if params_response.status_code == 200:
                        parameters = params_response.json()
                        connection_info['parameters'] = parameters
                    else:
                        connection_info['parameters'] = {}
                    
                    return connection_info
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to get connection details from {detail_url}: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Request failed: {e}")
                continue
        
        return {}

    def connection_exists(self, name: str) -> bool:
        """Check if a connection with the given name already exists"""
        connections = self.get_connections()
        if isinstance(connections, dict):
            # connections is a dict with identifiers as keys
            return any(conn.get('name') == name for conn in connections.values())
        return False

    def get_connection_groups(self) -> Dict:
        """Get list of existing connection groups"""
        if not self.auth_token:
            if not self.authenticate():
                return {}
        
        for groups_url in self._build_api_endpoints("connectionGroups"):
            try:
                response = self.session.get(groups_url)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to get connection groups from {groups_url}: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Request failed for {groups_url}: {e}")
                continue
        
        return {}
    
    def connection_exists_by_details(self, hostname: str, username: str, protocol: str) -> bool:
        """Check if a connection already exists with the same hostname, username, and protocol"""
        connections = self.get_connections()
        if isinstance(connections, dict):
            for conn in connections.values():
                params = conn.get('parameters', {})
                if (params.get('hostname') == hostname and 
                    params.get('username') == username and 
                    conn.get('protocol') == protocol):
                    return True
        return False

    def get_connection_by_name(self, name: str) -> Optional[Dict]:
        """Get connection details by name"""
        connections = self.get_connections()
        if isinstance(connections, dict):
            for conn in connections.values():
                if conn.get('name') == name:
                    return conn
        return None

    def get_connection_by_name_and_parent(self, name: str, parent_identifier: Optional[str] = None) -> Optional[Dict]:
        """Get connection details by name and parent identifier"""
        connections = self.get_connections()
        target_parent = parent_identifier or "ROOT"
        if isinstance(connections, dict):
            for conn in connections.values():
                if conn.get('name') == name and conn.get('parentIdentifier') == target_parent:
                    return conn
        return None

    def update_connection(self, identifier: str, name: str, hostname: str, username: str = "", 
                         password: str = "", port: int = 3389, protocol: str = "rdp",
                         enable_wol: bool = True, mac_address: str = "", 
                         parent_identifier: Optional[str] = None, rdp_settings: Optional[Dict[str, str]] = None,
                         wol_settings: Optional[Dict[str, str]] = None) -> bool:
        """Update an existing connection"""
        if not self.auth_token and not self.authenticate():
            return False

        if protocol == "rdp":
            connection_data = {
                "name": name,
                "protocol": "rdp",
                "parentIdentifier": parent_identifier or "ROOT",
                "parameters": {
                    "hostname": hostname,
                    "port": str(port),
                    "username": username,
                    "password": password,
                    "security": "any",
                    "ignore-cert": "true",
                    "enable-wallpaper": "true",
                    "enable-theming": "true", 
                    "enable-font-smoothing": "true",
                    "enable-full-window-drag": "true",
                    "enable-desktop-composition": "true",
                    "enable-menu-animations": "true",
                    "resize-method": "display-update"
                },
                "attributes": {
                    "max-connections": "2",
                    "max-connections-per-user": "1"
                }
            }
            
            # Apply RDP setting overrides if provided
            if rdp_settings:
                for key, value in rdp_settings.items():
                    if key.startswith('enable-'):
                        connection_data["parameters"][key] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                    else:
                        connection_data["parameters"][key] = value

            # Add Wake-on-LAN parameters if enabled
            if enable_wol and mac_address:
                wol_params = {
                    "wol-send-packet": "true",
                    "wol-mac-addr": mac_address,
                    "wol-broadcast-addr": "255.255.255.255",
                    "wol-udp-port": "9"
                }
                
                # Apply WoL setting overrides if provided
                if wol_settings:
                    for key, value in wol_settings.items():
                        if key == 'send-packet':
                            wol_params["wol-send-packet"] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                        elif key == 'broadcast-addr':
                            wol_params["wol-broadcast-addr"] = value
                        elif key == 'udp-port':
                            wol_params["wol-udp-port"] = str(value)
                
                connection_data["parameters"].update(wol_params)
        else:  # VNC
            # Default VNC parameters with enhanced options
            vnc_params = {
                "hostname": hostname,
                "port": str(port),
                "password": password,
                # Display and quality settings
                "color-depth": "32",
                "swap-red-blue": "false",
                "cursor": "local",
                "encoding": "tight",
                # Clipboard and input settings
                "enable-sftp": "false",
                "disable-copy": "false",
                "disable-paste": "false",
                # Performance optimizations
                "autoretry": "5",
                "read-only": "false"
            }
            
            connection_data = {
                "name": name,
                "protocol": "vnc",
                "parentIdentifier": parent_identifier or "ROOT",
                "parameters": vnc_params,
                "attributes": {
                    "max-connections": "2",
                    "max-connections-per-user": "1"
                }
            }
            
            if enable_wol and mac_address:
                wol_params = {
                    "wol-send-packet": "true",
                    "wol-mac-addr": mac_address,
                    "wol-broadcast-addr": "255.255.255.255",
                    "wol-udp-port": "9"
                }
                
                # Apply WoL setting overrides if provided
                if wol_settings:
                    for key, value in wol_settings.items():
                        if key == 'send-packet':
                            wol_params["wol-send-packet"] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                        elif key == 'broadcast-addr':
                            wol_params["wol-broadcast-addr"] = value
                        elif key == 'udp-port':
                            wol_params["wol-udp-port"] = str(value)
                
                connection_data["parameters"].update(wol_params)

        # Ensure payload includes identifier and activeConnections per API docs
        # activeConnections set to 0 for update operations
        connection_data.setdefault('identifier', identifier)
        connection_data.setdefault('activeConnections', 0)

        # Use explicit headers per API documentation
        headers = {
            'Content-Type': 'application/json;charset=utf-8',
            'Accept': 'application/json'
        }

        # Per documentation: only use the canonical PUT endpoint used by the Guacamole web UI
        canonical_url = urljoin(self.config.GUAC_BASE_URL, f"/api/session/data/postgresql/connections/{identifier}")

        try:
            # The client must send the Guacamole-Token header obtained from authenticate(); do not attempt method overrides
            if 'Guacamole-Token' not in self.session.headers:
                console.print(Panel("Guacamole-Token header missing - ensure authenticate() succeeded and the server supports header-based tokens", title="Update failed", border_style="red"))
                return False

            resp = self.session.put(canonical_url, json=connection_data, headers=headers)

            if resp.status_code in (200, 204):
                console.print(f"[green]Updated connection '{name}' (ID: {identifier})[/green]")
                return True
            else:
                console.print(Panel(f"Failed to update connection via canonical endpoint {canonical_url}: {resp.status_code}\n{resp.text}", title="Update failed", border_style="red"))
                return False

        except requests.exceptions.RequestException as e:
            console.print(Panel(f"Request error while updating connection via canonical endpoint: {e}", title="Update failed", border_style="red"))
            return False

    def delete_connection(self, identifier: str) -> bool:
        """Delete a connection by identifier"""
        if not self.auth_token and not self.authenticate():
            return False

        # Try different delete endpoints
        delete_endpoints = []
        
        # Build endpoints for deletion 
        for base_path in ["/api/session/data/postgresql", "/api/session/data/mysql", "/guacamole/api/session/data/postgresql", "/guacamole/api/session/data/mysql"]:
            delete_endpoints.append(f"{self.config.GUAC_BASE_URL}{base_path}/connections/{identifier}?token={self.auth_token}")

        for endpoint in delete_endpoints:
            try:
                response = self.session.delete(endpoint)
                if response.status_code in (200, 204):
                    return True
                elif response.status_code == 404:
                    continue
                else:
                    # Try alternative approach - some Guacamole versions need different method
                    continue
            except requests.exceptions.RequestException as e:
                continue

        return False

    def delete_connection_group(self, identifier: str) -> bool:
        """Delete a connection group by identifier"""
        if not self.auth_token and not self.authenticate():
            return False

        # Try different delete endpoints for connection groups
        delete_endpoints = []
        
        # Build endpoints for deletion 
        for base_path in ["/api/session/data/postgresql", "/api/session/data/mysql", "/guacamole/api/session/data/postgresql", "/guacamole/api/session/data/mysql"]:
            delete_endpoints.append(f"{self.config.GUAC_BASE_URL}{base_path}/connectionGroups/{identifier}?token={self.auth_token}")

        for endpoint in delete_endpoints:
            try:
                response = self.session.delete(endpoint)
                if response.status_code in (200, 204):
                    return True
                elif response.status_code == 404:
                    continue
                else:
                    continue
            except requests.exceptions.RequestException as e:
                continue

        return False

    def move_connection_to_group(self, connection_id: str, group_identifier: str) -> bool:
        """Move a connection to a specific group"""
        if not self.auth_token and not self.authenticate():
            return False

        # Get current connection details
        connection_details = self.get_connection_details(connection_id)
        if not connection_details:
            return False

        # Update the parentIdentifier to move to new group
        connection_data = connection_details.copy()
        connection_data['parentIdentifier'] = group_identifier

        # Try different update endpoints
        update_endpoints = []
        for base_path in ["/api/session/data/postgresql", "/api/session/data/mysql", "/guacamole/api/session/data/postgresql", "/guacamole/api/session/data/mysql"]:
            update_endpoints.append(f"{self.config.GUAC_BASE_URL}{base_path}/connections/{connection_id}?token={self.auth_token}")

        for endpoint in update_endpoints:
            try:
                response = self.session.put(endpoint, json=connection_data)
                if response.status_code in (200, 204):
                    return True
                elif response.status_code == 404:
                    continue
                else:
                    continue
            except requests.exceptions.RequestException as e:
                continue

        return False

    def create_connection_group(self, name: str, parent_identifier: str = "ROOT", group_type: str = "ORGANIZATIONAL") -> Optional[str]:
        """Create a connection group to organize multiple connections"""
        if not self.auth_token and not self.authenticate():
            return None

        payload = {
            "name": name,
            "parentIdentifier": parent_identifier,
            "type": group_type,
            "attributes": {
                "max-connections": "",
                "max-connections-per-user": "",
                "enable-session-affinity": ""
            }
        }

        for endpoint in self._build_api_endpoints("connectionGroups"):
            try:
                response = self.session.post(endpoint, json=payload)
                if response.status_code in [200, 201]:  # Accept both 200 and 201 as success
                    data = response.json()
                    identifier = data.get("identifier")
                    print(f"Created connection group '{name}' (ID: {identifier})")
                    return identifier
                elif response.status_code == 400 and "already exists" in response.text.lower():
                    # Group already exists - try to find its identifier
                    existing_groups = self.get_connection_groups()
                    for group in existing_groups.values() if isinstance(existing_groups, dict) else []:
                        if group.get('name') == name:
                            print(f"Using existing connection group '{name}' (ID: {group.get('identifier')})")
                            return group.get('identifier')
                    print(f"Warning: Group '{name}' exists but couldn't find ID - connections will be created at root level")
                    return None
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to create group: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Request failed for group creation: {e}")
                continue

        print("Unable to create connection group")
        return None
    
    def create_rdp_connection(self, name: str, hostname: str, username: str = "", password: str = "", 
                            port: int = 3389, enable_wol: bool = True, mac_address: str = "",
                            parent_identifier: Optional[str] = None, rdp_settings: Optional[Dict[str, str]] = None,
                            wol_settings: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Create RDP connection in Guacamole"""
        if not self.auth_token:
            if not self.authenticate():
                return None
        
        connection_data = {
            "name": name,
            "protocol": "rdp",
            "parentIdentifier": parent_identifier or "ROOT",
            "parameters": {
                "hostname": hostname,
                "port": str(port),
                "username": username,
                "password": password,
                "security": "any",
                "ignore-cert": "true",
                "enable-wallpaper": "true",
                "enable-theming": "true", 
                "enable-font-smoothing": "true",
                "enable-full-window-drag": "true",
                "enable-desktop-composition": "true",
                "enable-menu-animations": "true",
                "resize-method": "display-update"
            },
            "attributes": {
                "max-connections": "2",
                "max-connections-per-user": "1"
            }
        }
        
        # Apply RDP setting overrides if provided
        if rdp_settings:
            for key, value in rdp_settings.items():
                if key.startswith('enable-'):
                    # Convert to boolean
                    connection_data["parameters"][key] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                else:
                    connection_data["parameters"][key] = value

        # Add Wake-on-LAN parameters if enabled
        if enable_wol and mac_address:
            wol_params = {
                "wol-send-packet": "true",
                "wol-mac-addr": mac_address,
                "wol-broadcast-addr": "255.255.255.255",
                "wol-udp-port": "9"
            }
            
            # Apply WoL setting overrides if provided
            if wol_settings:
                for key, value in wol_settings.items():
                    if key == "send-packet":
                        wol_params["wol-send-packet"] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                    elif key.startswith("wol-"):
                        wol_params[key] = value
                    else:
                        wol_params[f"wol-{key}"] = value
            
            connection_data["parameters"].update(wol_params)
        
        for endpoint in self._build_api_endpoints("connections"):
            try:
                response = self.session.post(endpoint, json=connection_data)
                if response.status_code in (200, 201):
                    data = response.json()
                    identifier = data.get("identifier")
                    print(f"Successfully created RDP connection '{name}' (ID: {identifier})")
                    return identifier
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to create RDP connection via {endpoint}: {response.status_code} {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to create RDP connection via {endpoint}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response: {e.response.text}")
                continue

        return None
    
    def create_vnc_connection(self, name: str, hostname: str, password: str = "", 
                            port: int = 5900, enable_wol: bool = True, mac_address: str = "",
                            parent_identifier: Optional[str] = None, wol_settings: Optional[Dict[str, str]] = None,
                            vnc_settings: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Create VNC connection in Guacamole"""
        if not self.auth_token:
            if not self.authenticate():
                return None
        
        # Default VNC parameters with enhanced options
        vnc_params = {
            "hostname": hostname,
            "port": str(port),
            "password": password,
            # Display and quality settings
            "color-depth": "32",
            "swap-red-blue": "false",
            "cursor": "local",
            "encoding": "tight",
            # Clipboard and input settings
            "enable-sftp": "false",
            "disable-copy": "false",
            "disable-paste": "false",
            # Performance optimizations
            "autoretry": "5",
            "read-only": "false"
        }
        
        # Apply VNC setting overrides if provided
        if vnc_settings:
            for key, value in vnc_settings.items():
                if key.startswith('enable-') or key.startswith('disable-'):
                    vnc_params[key] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                else:
                    vnc_params[key] = value
        
        connection_data = {
            "name": name,
            "protocol": "vnc",
            "parentIdentifier": parent_identifier or "ROOT",
            "parameters": vnc_params,
            "attributes": {
                "max-connections": "2",
                "max-connections-per-user": "1"
            }
        }
        
        # Add Wake-on-LAN parameters if enabled
        if enable_wol and mac_address:
            wol_params = {
                "wol-send-packet": "true",
                "wol-mac-addr": mac_address,
                "wol-broadcast-addr": "255.255.255.255",
                "wol-udp-port": "9"
            }
            
            # Apply WoL setting overrides if provided
            if wol_settings:
                for key, value in wol_settings.items():
                    if key == "send-packet":
                        wol_params["wol-send-packet"] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                    elif key.startswith("wol-"):
                        wol_params[key] = value
                    else:
                        wol_params[f"wol-{key}"] = value
            
            connection_data["parameters"].update(wol_params)
        
        for endpoint in self._build_api_endpoints("connections"):
            try:
                response = self.session.post(endpoint, json=connection_data)
                if response.status_code in (200, 201):
                    data = response.json()
                    identifier = data.get("identifier")
                    print(f"Successfully created VNC connection '{name}' (ID: {identifier})")
                    return identifier
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to create VNC connection via {endpoint}: {response.status_code} {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to create VNC connection via {endpoint}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response: {e.response.text}")
                continue

        return None

    def create_ssh_connection(self, name: str, hostname: str, username: str, password: str = "", 
                             port: int = 22, enable_wol: bool = False, mac_address: str = "",
                             parent_identifier: Optional[str] = None, wol_settings: Optional[Dict] = None) -> Optional[str]:
        """Create SSH connection in Guacamole"""
        if not self.authenticate():
            return None

        connection_data = {
            "name": name,
            "protocol": "ssh",
            "parentIdentifier": parent_identifier or "ROOT",
            "parameters": {
                "hostname": hostname,
                "port": str(port),
                "username": username,
                "color-scheme": "gray-black",  # Better readability
                "font-name": "monospace",
                "font-size": "12",
                "enable-sftp": "true",  # Enable file transfer
                "sftp-directory": "/home/" + username  # Default to user home
            },
            "attributes": {
                "max-connections": "2",
                "max-connections-per-user": "1"
            }
        }
        
        # Add password if provided
        if password:
            connection_data["parameters"]["password"] = password
        
        # Add Wake-on-LAN parameters if enabled
        if enable_wol and mac_address:
            wol_params = {
                "wol-send-packet": "true",
                "wol-mac-addr": mac_address,
                "wol-broadcast-addr": "255.255.255.255",
                "wol-udp-port": "9"
            }
            
            # Apply WoL setting overrides if provided
            if wol_settings:
                for key, value in wol_settings.items():
                    if key == "send-packet":
                        wol_params["wol-send-packet"] = "true" if value.lower() in ['true', '1', 'yes'] else "false"
                    elif key.startswith("wol-"):
                        wol_params[key] = value
                    else:
                        wol_params[f"wol-{key}"] = value
            
            connection_data["parameters"].update(wol_params)
        
        for endpoint in self._build_api_endpoints("connections"):
            try:
                response = self.session.post(endpoint, json=connection_data)
                if response.status_code in (200, 201):
                    data = response.json()
                    identifier = data.get("identifier")
                    print(f"Successfully created SSH connection '{name}' (ID: {identifier})")
                    return identifier
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to create SSH connection via {endpoint}: {response.status_code} {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to create SSH connection via {endpoint}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response: {e.response.text}")
                continue

        return None

class ProxmoxAPI:
    """Handles Proxmox API interactions"""
    
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.verify = False  # For self-signed certificates
        self.session.headers.update({
            'Authorization': f'PVEAPIToken={self.config.PROXMOX_TOKEN_ID}={self.config.PROXMOX_SECRET}'
        })
        
    def test_auth(self) -> bool:
        """Test Proxmox API authentication"""
        try:
            response = self.session.get(f"{self.config.proxmox_base_url}/version")
            if response.status_code == 200:
                return True
            else:
                return False
        except requests.exceptions.RequestException as e:
            return False
    
    def get_nodes(self) -> List[Dict]:
        """Get list of Proxmox nodes"""
        nodes_url = f"{self.config.proxmox_base_url}/nodes"
        
        try:
            response = self.session.get(nodes_url)
            response.raise_for_status()
            data = response.json()
            nodes = data.get('data', [])
            return nodes
        except requests.exceptions.RequestException as e:
            print(f"Failed to get nodes: {e}")
            return []
    
    def get_node_ips(self) -> List[str]:
        """Get IP addresses of all Proxmox nodes"""
        nodes = self.get_nodes()
        node_ips = []
        
        for node_info in nodes:
            node_name = node_info['node']
            network_url = f"{self.config.proxmox_base_url}/nodes/{node_name}/network"
            
            try:
                response = self.session.get(network_url)
                response.raise_for_status()
                data = response.json()
                interfaces = data.get('data', [])
                
                # Extract IP addresses from network interfaces
                for interface in interfaces:
                    if 'cidr' in interface and interface.get('type') == 'bridge':
                        # Parse CIDR notation to get IP
                        cidr = interface['cidr']
                        if '/' in cidr:
                            ip = cidr.split('/')[0]
                            if ip not in node_ips:
                                node_ips.append(ip)
            except requests.exceptions.RequestException as e:
                # If network endpoint fails, try to get IP from node status
                try:
                    status_url = f"{self.config.proxmox_base_url}/nodes/{node_name}/status"
                    response = self.session.get(status_url)
                    response.raise_for_status()
                    data = response.json()
                    node_data = data.get('data', {})
                    
                    # Some Proxmox versions include IP in status
                    if 'ip' in node_data:
                        ip = node_data['ip']
                        if ip not in node_ips:
                            node_ips.append(ip)
                except requests.exceptions.RequestException:
                    pass  # Skip this node if we can't get IP
        
        return node_ips
    
    def get_vms(self, node: Optional[str] = None) -> List[Dict]:
        """Get list of VMs from all nodes or specific node"""
        all_vms = []
        
        if node:
            nodes = [{'node': node}]
        else:
            nodes = self.get_nodes()
        
        for node_info in nodes:
            node_name = node_info['node']
            vms_url = f"{self.config.proxmox_base_url}/nodes/{node_name}/qemu"
            
            try:
                response = self.session.get(vms_url)
                response.raise_for_status()
                data = response.json()
                vms = data.get('data', [])
                
                # Add node information to each VM
                for vm in vms:
                    vm['node'] = node_name
                
                all_vms.extend(vms)
            except requests.exceptions.RequestException as e:
                print(f"Failed to get VMs from node {node_name}: {e}")
        
        return all_vms
    
    def get_vm_config(self, node: str, vmid: int) -> Dict:
        """Get VM configuration including network information"""
        config_url = f"{self.config.proxmox_base_url}/nodes/{node}/qemu/{vmid}/config"
        
        try:
            response = self.session.get(config_url)
            response.raise_for_status()
            data = response.json()
            return data.get('data', {})
        except requests.exceptions.RequestException as e:
            print(f"Failed to get VM config: {e}")
            return {}
    
    def update_vm_notes(self, node: str, vmid: int, notes: str) -> bool:
        """Update VM notes in Proxmox"""
        config_url = f"{self.config.proxmox_base_url}/nodes/{node}/qemu/{vmid}/config"
        
        try:
            data = {
                'description': notes
            }
            response = self.session.put(config_url, data=data)
            if response.status_code in (200, 204):
                console.print(f"[green]Updated VM {vmid} notes with encrypted passwords[/green]")
                return True
            elif response.status_code == 405:
                # Some proxies disallow PUT - try POST with override
                try:
                    console.print(f"[yellow]PUT rejected for {config_url} (405). Trying POST override...[/yellow]")
                    headers = {'X-HTTP-Method-Override': 'PUT'}
                    r = self.session.post(config_url, data=data, headers=headers)
                    if r.status_code in (200, 201, 204):
                        console.print(f"[green]Updated VM {vmid} notes via POST override[/green]")
                        return True
                    r2 = self.session.post(f"{config_url}?_method=PUT", data=data)
                    if r2.status_code in (200, 201, 204):
                        console.print(f"[green]Updated VM {vmid} notes via POST?_method=PUT[/green]")
                        return True
                    console.print(Panel(f"Failed to update VM notes via override: {r.status_code}\n{r.text}", title="VM note update failed", border_style="red"))
                    return False
                except requests.exceptions.RequestException as e:
                    console.print(Panel(f"Failed to update VM notes via override: {e}", title="VM note update failed", border_style="red"))
                    return False
            else:
                console.print(Panel(f"Failed to update VM notes: {response.status_code}\n{response.text}", title="VM note update failed", border_style="red"))
                return False
        except requests.exceptions.RequestException as e:
            console.print(Panel(f"Failed to update VM notes: {e}", title="VM note update failed", border_style="red"))
            return False
    
    def get_vm_notes(self, node: str, vmid: int) -> str:
        """Get VM notes/description and automatically encrypt passwords if needed"""
        config = self.get_vm_config(node, vmid)
        # Notes can be in 'description' or 'notes' field, and may be URL-encoded
        notes = config.get('description', '') or config.get('notes', '')
        
        if notes:
            # URL-decode the notes (Proxmox often URL-encodes them)
            try:
                from urllib.parse import unquote
                notes = unquote(notes)
            except Exception:
                pass  # If decoding fails, use original
        
        # Process notes to encrypt passwords and update VM if needed
        if notes:
            notes = self.process_and_update_vm_notes(node, vmid, notes)
        
        return notes
    
    def parse_credentials_from_notes(self, notes: str, vm_name: str = "", vm_id: str = "unknown", vm_node: str = "unknown", vm_ip: str = "unknown") -> List[Dict[str, str]]:
        """Parse user credentials from VM notes - one-line format only"""
        credentials = []
        
        if not notes:
            return credentials
        
        import re
        import socket
        
        # Get additional variables for templates (passed as parameters)
        hostname = socket.gethostname().split('.')[0]  # Local hostname
        
        # New flexible format: Parameters can be in any order, multiple protocols per user
        # Example: user:"admin" pass:"pass123" protos:"rdp,vnc,ssh" rdp_port:"3389" vnc_port:"5901" ssh_port:"22" confName:"template" wolDisabled:"true";
        # Find lines ending with semicolon (credential lines)
        credential_lines = re.findall(r'[^;]*;', notes, re.MULTILINE)
        
        # Also look for default template (handle various formats)
        default_template_pattern = r'default_conf_name:\s*["\']([^"\']+)["\']'
        default_template = None
        default_match = re.search(default_template_pattern, notes, re.IGNORECASE)
        if default_match:
            default_template = default_match.group(1).strip()
        
        # Filter out non-credential lines (like default_conf_name)
        credential_lines = [line for line in credential_lines if not line.strip().startswith('default_conf_name')]
        
        # Process each credential line
        for line in credential_lines:
            line = line.strip()
            if not line or line == ';':
                continue
                
            # Parse key-value pairs from the line
            params = self._parse_credential_line(line)
            if not params:
                print(f"  No parameters parsed from line: {line}")
                continue
                
            # Handle malformed lines where encrypted_password got concatenated with confName
            if 'confName' in params and 'encrypted_password:' in params['confName']:
                confname_value = params['confName']
                if ' encrypted_password:' in confname_value:
                    # Split at the encrypted_password part
                    parts = confname_value.split(' encrypted_password:', 1)
                    if len(parts) == 2:
                        params['confName'] = parts[0].strip()
                        # The encrypted password might be at the end of the line
                        # Look for it after the current confName value in the original line
                        enc_pass_match = re.search(r'encrypted_password:["\']*([^"\';\s]+)', line)
                        if enc_pass_match:
                            params['encrypted_password'] = enc_pass_match.group(1)
                
            # Extract required parameters with fallbacks (support both new and old names)
            username = params.get('username', params.get('user', '')).strip()
            password = params.get('password', params.get('pass', '')).strip()
            encrypted_password = params.get('encrypted_password', '').strip()
            protocols_str = params.get('protocols', params.get('protos', params.get('proto', ''))).strip()
            
            # Handle password decryption if encrypted
            if encrypted_password and not password:
                password = self._decrypt_password(encrypted_password)
                if not password:
                    print(f"  Failed to decrypt password for user {username}")
                    continue
            
            # More detailed error reporting for missing fields
            missing_fields = []
            if not username:
                missing_fields.append("username")
            if not password and not encrypted_password:
                missing_fields.append("password")
            if not protocols_str:
                missing_fields.append("protocols")
                
            if missing_fields:
                print(f"  Skipping credential line (missing: {', '.join(missing_fields)})")
                print(f"    Parsed params: {params}")
                print(f"    Original line: {line}")
                continue
                
            # Parse protocols (can be comma-separated)
            protocols = [p.strip().lower() for p in protocols_str.split(',') if p.strip()]
            
            # Validate protocols
            valid_protocols = []
            for proto in protocols:
                if proto in ['rdp', 'vnc', 'ssh']:
                    valid_protocols.append(proto)
                else:
                    print(f"Warning: Unsupported protocol '{proto}' for user {username}. Skipping protocol.")
            
            if not valid_protocols:
                print(f"Warning: No valid protocols found for user {username}. Skipping.")
                continue
            
            # Create connections for each protocol
            for protocol in valid_protocols:
                # Get protocol-specific port with fallbacks
                port_key = f"{protocol}_port"
                if protocol == 'rdp':
                    default_port = 3389
                elif protocol == 'ssh':
                    default_port = 22
                else:  # vnc
                    default_port = 5900
                
                port = int(params.get(port_key, params.get('port', default_port)))
                
                # Parse RDP settings if provided (support both new and old names)
                rdp_overrides = {}
                rdp_settings = params.get('rdp_settings', params.get('rdpSettings', ''))
                if rdp_settings and protocol == 'rdp':
                    for setting in rdp_settings.split(','):
                        if '=' in setting:
                            key, value = setting.split('=', 1)
                            rdp_overrides[key.strip()] = value.strip()
                
                # Parse VNC settings if provided (support both new and old names)
                vnc_overrides = {}
                vnc_settings = params.get('vnc_settings', params.get('vncSettings', ''))
                if vnc_settings and protocol == 'vnc':
                    for setting in vnc_settings.split(','):
                        if '=' in setting:
                            key, value = setting.split('=', 1)
                            vnc_overrides[key.strip()] = value.strip()
                
                # Parse WoL settings if provided (support both new and old names)
                wol_overrides = {}
                wol_settings = params.get('wol_settings', params.get('wolSettings', ''))
                if wol_settings:
                    for setting in wol_settings.split(','):
                        if '=' in setting:
                            key, value = setting.split('=', 1)
                            wol_overrides[key.strip()] = value.strip()
                
                # Check if WoL is disabled for this connection (support both new and old names)
                wol_disabled_str = params.get('wol_disabled', params.get('wolDisabled', 'false')).lower()
                wol_disabled = wol_disabled_str in ['true', '1', 'yes']
                
                # Determine connection name template (support both new and old names)
                custom_name = params.get('connection_name', params.get('confName'))
                if custom_name:
                    template = custom_name
                elif default_template:
                    template = default_template
                else:
                    template = "{user}@{vmname}-{proto}"  # Default fallback
                
                # Process all available placeholders
                placeholders = {
                    'vmname': vm_name,
                    'user': username,
                    'username': username,
                    'password': password,
                    'proto': protocol,
                    'protocol': protocol,
                    'vmid': str(vm_id),
                    'vm_id': str(vm_id),
                    'node': vm_node,
                    'vmnode': vm_node,
                    'vm_node': vm_node,
                    'ip': vm_ip,
                    'vmip': vm_ip,
                    'vm_ip': vm_ip,
                    'hostname': hostname,
                    'host': hostname,
                    'port': str(port)
                }
                
                # Replace placeholders in template
                connection_name = template
                for key, value in placeholders.items():
                    connection_name = connection_name.replace('{' + key + '}', str(value))
                

                
                credentials.append({
                    'username': username,
                    'password': password,
                    'protocol': protocol,
                    'connection_name': connection_name,
                    'port': port,
                    'rdp_settings': rdp_overrides,
                    'vnc_settings': vnc_overrides,
                    'wol_settings': wol_overrides,
                    'wol_disabled': wol_disabled
                })
        
        return credentials

    def has_structured_credentials(self, notes: str) -> bool:
        """Return True if notes contain at least one properly structured credential line.

        A structured credential line is defined as a line ending with ';' that contains
        at minimum one of user:/username: plus pass:/password: (or encrypted_password:)
        and a protocols/protos/proto field. Legacy lines like 'user:pass' without a
        terminating semicolon MUST NOT be treated as structured credentials.
        """
        if not notes:
            return False
        # Fast fail: need a semicolon to be considered structured
        if ';' not in notes:
            return False
        # Re-use existing parser logic: if any credential objects return, we have structured lines
        parsed = self.parse_credentials_from_notes(notes)
        return len(parsed) > 0

    def _parse_credential_line(self, line: str) -> Dict[str, str]:
        """Parse a credential line with flexible parameter order"""
        params = {}
        
        # Remove trailing semicolon and whitespace
        line = line.rstrip(';').strip()
        
        # Enhanced pattern to handle quoted values with embedded colons and parameters
        # This pattern is more careful about matching quoted strings that may contain colons
        param_pattern = r'(\w+):\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s;"\']+))'
        
        matches = re.finditer(param_pattern, line)
        for match in matches:
            key = match.group(1).strip()
            # Use the appropriate captured group (quoted or unquoted)
            value = match.group(2) or match.group(3) or match.group(4)
            if value:
                params[key] = value.strip()
        
        return params

    def _get_encryption_key(self) -> Optional[bytes]:
        """Get or generate encryption key from config"""
        encryption_key = getattr(self.config, 'ENCRYPTION_KEY', None)
        if not encryption_key:
            print("Warning: No encryption key found in config. Passwords will not be encrypted.")
            return None
        
        # Convert string key to bytes and derive a proper 32-byte key
        key_bytes = encryption_key.encode('utf-8')
        return base64.urlsafe_b64encode(hashlib.sha256(key_bytes).digest())

    def _encrypt_password(self, password: str) -> str:
        """Encrypt a password using Fernet encryption"""
        try:
            key = self._get_encryption_key()
            if not key:
                return password  # Return plain if no key
            
            fernet = Fernet(key)
            encrypted = fernet.encrypt(password.encode('utf-8'))
            return base64.urlsafe_b64encode(encrypted).decode('utf-8')
        except Exception as e:
            print(f"Warning: Failed to encrypt password: {e}")
            return password

    def _decrypt_password(self, encrypted_password: str) -> Optional[str]:
        """Decrypt a password using Fernet encryption"""
        try:
            key = self._get_encryption_key()
            if not key:
                print("Warning: No encryption key available for decryption")
                return None
            
            fernet = Fernet(key)
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_password.encode('utf-8'))
            decrypted = fernet.decrypt(encrypted_bytes)
            return decrypted.decode('utf-8')
        except Exception as e:
            print(f"Warning: Failed to decrypt password: {e}")
            return None

    def encrypt_credentials_in_notes(self, notes: str) -> str:
        """Encrypt all passwords in VM notes and return updated notes"""
        if not notes:
            return notes
            
        lines = notes.split('\n')
        updated_lines = []
        changes_made = False
        
        for line in lines:
            if ';' in line and ('password:' in line.lower() or 'pass:' in line.lower()):
                # Parse and encrypt passwords in this line
                params = self._parse_credential_line(line + ';' if not line.endswith(';') else line)
                if params:
                    password = params.get('password', params.get('pass', ''))
                    if password and 'encrypted_password' not in params:
                        encrypted = self._encrypt_password(password)
                        if encrypted:
                            # Replace the password with encrypted_password
                            import re
                            # Match quoted passwords more carefully
                            password_pattern = f'pass:"{re.escape(password)}"'
                            password_pattern_alt = f'password:"{re.escape(password)}"'
                            
                            if f'pass:"{password}"' in line:
                                line = line.replace(f'pass:"{password}"', f'encrypted_password:"{encrypted}"')
                                changes_made = True
                            elif f'password:"{password}"' in line:
                                line = line.replace(f'password:"{password}"', f'encrypted_password:"{encrypted}"')
                                changes_made = True
                        
            updated_lines.append(line)
        
        if changes_made:
            print("Converted plain passwords to encrypted format in VM notes")
            
        return '\n'.join(updated_lines)
    
    def process_and_update_vm_notes(self, node: str, vmid: int, notes: str) -> str:
        """
        Process VM notes to encrypt passwords and update VM if changes are made.
        Returns the processed notes string.
        """
        import re
        
        if not notes:
            return notes
        
        original_notes = notes
        updated_notes = notes
        changes_made = False
        
        # Process each line for password encryption
        lines = notes.split('\n')
        updated_lines = []
        
        for line in lines:
            original_line = line
            
            # Check if line contains credentials
            if ';' in line and any(param in line.lower() for param in ['user:', 'pass:', 'encrypted_password:']):
                params = self._parse_credential_line(line)
                if params:
                    plain_password = params.get('password', params.get('pass', ''))
                    encrypted_password = params.get('encrypted_password', '')
                    
                    # Case 1: Has plain password but no encrypted password -> encrypt and replace
                    if plain_password and not encrypted_password:
                        encrypted = self._encrypt_password(plain_password)
                        if encrypted:
                            # Remove plain password and add encrypted password
                            new_line = line
                            # Remove password field (both formats)
                            new_line = re.sub(r'\bpass:"[^"]*"', '', new_line)
                            new_line = re.sub(r'\bpassword:"[^"]*"', '', new_line)
                            # Clean up extra spaces
                            new_line = re.sub(r'\s+', ' ', new_line).strip()
                            # Add encrypted password before the semicolon
                            new_line = new_line.rstrip(';').strip() + f' encrypted_password:"{encrypted}";'
                            line = new_line
                            changes_made = True
                            print(f"Encrypted password for VM {vmid}")
                    
                    # Case 2: Has both plain and encrypted password -> check if they match
                    elif plain_password and encrypted_password:
                        decrypted = self._decrypt_password(encrypted_password)
                        if decrypted != plain_password:
                            # Password changed - update encrypted password
                            new_encrypted = self._encrypt_password(plain_password)
                            if new_encrypted:
                                # Replace the encrypted password
                                new_line = re.sub(
                                    r'encrypted_password:"[^"]*"',
                                    f'encrypted_password:"{new_encrypted}"',
                                    line
                                )
                                # Remove plain password
                                new_line = re.sub(r'\bpass:"[^"]*"', '', new_line)
                                new_line = re.sub(r'\bpassword:"[^"]*"', '', new_line)
                                # Clean up extra spaces
                                new_line = re.sub(r'\s+', ' ', new_line).strip()
                                line = new_line
                                changes_made = True
                                print(f"Updated encrypted password for VM {vmid} (password changed)")
                    
                    # Case 3: Only encrypted password -> leave as is (this is the desired state)
                    
            updated_lines.append(line)
        
        updated_notes = '\n'.join(updated_lines)
        
        # If changes were made, update the VM notes in Proxmox
        if changes_made and updated_notes != original_notes:
            if self.update_vm_notes(node, vmid, updated_notes):
                print(f"Successfully updated VM {vmid} notes with encrypted passwords")
            else:
                print(f"Warning: Failed to update VM {vmid} notes in Proxmox")
                return original_notes  # Return original if update failed
        
        return updated_notes
    
    def get_vm_agent_network(self, node: str, vmid: int) -> List[Dict]:
        """Fetch network information via QEMU guest agent if available"""
        agent_url = f"{self.config.proxmox_base_url}/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces"
        try:
            # Use GET and include X-Requested-With to mirror the browser/UI request
            headers = {'X-Requested-With': 'XMLHttpRequest'}
            response = self.session.get(agent_url, headers=headers, timeout=10)
            if response.status_code != 200:
                # Provide richer diagnostic output for failed guest agent queries
                resp_text = "<no body>"
                try:
                    resp_text = response.text
                except Exception:
                    pass
                print(
                    f" Guest agent query returned status {response.status_code} for VM {vmid}: {resp_text}"
                )

                # Common cause: guest agent not available or VM type doesn't support this endpoint
                if response.status_code == 501:
                    print(
                        "  Guest agent endpoint not implemented (501).\n"
                        "  If this is a QEMU VM with qemu-guest-agent installed, ensure the agent is running inside the VM and that Proxmox has guest agent support enabled.\n"
                        "  For LXC containers, the guest agent endpoints differ and may not be available via this path.\n"
                    )
                return []
            data = response.json()
            result = data.get('data', {})
            # Some responses wrap in {'result': [...]} while older return list directly
            interfaces = result.get('result') if isinstance(result, dict) else result
            if not isinstance(interfaces, list):
                print(f" Guest agent returned non-list data for VM {vmid}: {type(interfaces)}")
                return []
            
            # Debug: show what we got from guest agent
            valid_interfaces = []
            for iface in interfaces:
                name = iface.get('name', 'unknown')
                mac = iface.get('hardware-address', 'no-mac')
                ip_count = len(iface.get('ip-addresses', []))
                
                # Skip loopback interfaces
                if 'loopback' in name.lower() or 'pseudo-interface' in name.lower():
                    continue
                    
                print(f" Guest agent interface: {name} (MAC: {mac}, {ip_count} IPs)")
                valid_interfaces.append(iface)
                
            return valid_interfaces
        except requests.exceptions.RequestException as e:
            print(f"Warning: Guest agent network query failed for VM {vmid}: {e}")
            return []

    def get_vm_status(self, node: str, vmid: int) -> Dict:
        """Get VM status information"""
        status_url = f"{self.config.proxmox_base_url}/nodes/{node}/qemu/{vmid}/status/current"
        
        try:
            response = self.session.get(status_url)
            response.raise_for_status()
            data = response.json()
            return data.get('data', {})
        except requests.exceptions.RequestException as e:
            print(f"Failed to get VM status: {e}")
            return {}
    
    def start_vm(self, node: str, vmid: int) -> bool:
        """Start a VM"""
        start_url = f"{self.config.proxmox_base_url}/nodes/{node}/qemu/{vmid}/status/start"
        
        try:
            response = self.session.post(start_url)
            response.raise_for_status()
            print(f"Started VM {vmid} on node {node}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Failed to start VM {vmid}: {e}")
            return False
    
    def stop_vm(self, node: str, vmid: int) -> bool:
        """Stop a VM"""
        stop_url = f"{self.config.proxmox_base_url}/nodes/{node}/qemu/{vmid}/status/stop"
        
        try:
            response = self.session.post(stop_url)
            response.raise_for_status()
            print(f"Stopped VM {vmid} on node {node}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Failed to stop VM {vmid}: {e}")
            return False
    
    def get_vm_network_info(self, node: str, vmid: int) -> List[Dict]:
        """Extract network interface information including MAC and IP details"""
        config = self.get_vm_config(node, vmid)
        network_interfaces: List[Dict] = []

        # Parse static config (net0, net1, ...)
        for key, value in config.items():
            if key.startswith('net') and isinstance(value, str):
                net_info: Dict[str, Optional[str]] = {
                    'interface': key,
                    'mac': None,
                    'model': None,
                    'bridge': None,
                    'tag': None
                }

                parts = value.split(',')
                for part in parts:
                    if '=' in part:
                        k, v = part.split('=', 1)
                        net_info[k] = v
                        # Also check if this is a MAC address
                        if ':' in v and len(v.split(':')) == 6:
                            net_info['mac'] = v
                    else:
                        candidate = part.strip()
                        if ':' in candidate and len(candidate.split(':')) == 6:
                            net_info['mac'] = candidate
                        else:
                            net_info['model'] = candidate

                network_interfaces.append(net_info)

        # Attempt to enrich with guest agent data for live IPs
        agent_interfaces = self.get_vm_agent_network(node, vmid)
        agent_by_mac: Dict[str, Dict] = {}
        for iface in agent_interfaces:
            hardware_mac = iface.get('hardware-address')
            if not hardware_mac:
                continue
            ips = []
            for addr in iface.get('ip-addresses', []):
                ip_address = addr.get('ip-address')
                # Skip link-local, loopback, and IPv6 addresses
                if not ip_address:
                    continue
                if ip_address.startswith('127.') or ip_address.startswith('::1'):
                    continue
                # Skip all IPv6 addresses
                if '::' in ip_address or (':' in ip_address and '.' not in ip_address):
                    continue
                ips.append({
                    'address': ip_address,
                    'prefix': addr.get('prefix')
                })
            agent_by_mac[hardware_mac.lower()] = {
                'name': iface.get('name'),
                'ips': ips
            }

        enriched_interfaces: List[Dict] = []
        seen_macs = set()
        for net in network_interfaces:
            mac = (net.get('mac') or '').lower() if net.get('mac') else ''
            if mac in agent_by_mac:
                net['ip_addresses'] = agent_by_mac[mac]['ips']
                net['guest_interface'] = agent_by_mac[mac]['name']
            else:
                net['ip_addresses'] = []
            if mac:
                seen_macs.add(mac)
            enriched_interfaces.append(net)

        # Include any agent interfaces not present in config (e.g., hotplugged)
        for mac, details in agent_by_mac.items():
            if mac in seen_macs:
                continue
            enriched_interfaces.append({
                'interface': details.get('name'),
                'mac': mac,
                'model': 'agent',
                'bridge': None,
                'tag': None,
                'ip_addresses': details.get('ips', []),
                'guest_interface': details.get('name')
            })

        return enriched_interfaces

class NetworkScanner:
    """Network scanning functionality to find MAC addresses"""
    
    @staticmethod
    def get_local_network_range() -> Optional[str]:
        """Get the local network range (e.g., 192.168.1.0/24)"""
        try:
            # Get default gateway on macOS
            result = subprocess.run(['route', '-n', 'get', 'default'], 
                                  capture_output=True, text=True, timeout=10)
            
            gateway_match = re.search(r'gateway: (\d+\.\d+\.\d+\.\d+)', result.stdout)
            if not gateway_match:
                return None
            
            gateway = gateway_match.group(1)
            # Assume /24 network for simplicity
            network_parts = gateway.split('.')
            network_base = '.'.join(network_parts[:3]) + '.0/24'
            return network_base
        except Exception as e:
            print(f"Warning: Could not determine local network range: {e}")
            return None
    
    @staticmethod
    def scan_arp_table(target_mac: Optional[str] = None) -> List[Dict[str, str]]:
        """Scan ARP table for MAC addresses"""
        arp_entries = []
        try:
            # Try faster arp command first
            result = subprocess.run(['arp', '-an'], capture_output=True, text=True, timeout=2)
            if result.returncode != 0:
                # Fallback to regular arp -a
                result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=3)
            
            for line in result.stdout.split('\n'):
                # Parse ARP entries - handle multiple formats:
                # Format 1: host (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on en0
                # Format 2: ? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0
                # Handle MAC addresses with or without leading zeros (9c:6b:0:8e vs 9c:6b:00:8e)
                match = re.search(r'(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([a-fA-F0-9:]+)', line)
                if match:
                    hostname, ip, mac = match.groups()
                    
                    # Validate MAC address format (should be exactly 6 groups of 2 hex chars)
                    mac_parts = mac.lower().split(':')
                    if len(mac_parts) != 6:
                        continue  # Skip invalid MAC formats
                        
                    # Normalize MAC address - ensure consistent format with leading zeros
                    try:
                        mac_normalized = ':'.join(part.zfill(2) for part in mac_parts)
                        # Validate each part is valid hex
                        for part in mac_parts:
                            int(part, 16)
                    except ValueError:
                        continue  # Skip invalid hex in MAC
                    
                    entry = {
                        'hostname': hostname if hostname != '?' else ip,
                        'ip': ip,
                        'mac': mac_normalized
                    }
                    
                    # If looking for specific MAC, check match with detailed debugging
                    if target_mac:
                        target_parts = target_mac.lower().replace('-', ':').split(':')
                        target_normalized = ':'.join(part.zfill(2) for part in target_parts)
                        
                        if mac_normalized == target_normalized:
                            print(f" MAC match found: {target_normalized} -> {ip} ({hostname})")
                            return [entry]  # Return immediately if found
                        # Don't spam debug output for non-matches in normal operation
                    else:
                        # Only add to entries if we're not looking for a specific MAC
                        arp_entries.append(entry)
                    
        except Exception as e:
            print(f"Warning: Could not scan ARP table: {e}")
        
        # If looking for specific MAC and we reach here, it wasn't found
        if target_mac:
            return []
        
        return arp_entries
    
    @staticmethod
    def ping_sweep_network(network_range: str) -> None:
        """Ping sweep to populate ARP table"""
        try:
            network = ipaddress.IPv4Network(network_range, strict=False)
            print(f"Scanning network {network_range} to populate ARP table...")
            
            # Ping a range of IPs to populate ARP table
            processes = []
            for ip in list(network.hosts())[:50]:  # Limit to first 50 hosts
                try:
                    proc = subprocess.Popen(['ping', '-c', '1', '-W', '1000', str(ip)], 
                                          stdout=subprocess.DEVNULL, 
                                          stderr=subprocess.DEVNULL)
                    processes.append(proc)
                except Exception:
                    continue
            
            # Wait for pings to complete
            for proc in processes:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            
            print("Network scan completed")
            
        except Exception as e:
            print(f"Warning: Network ping sweep failed: {e}")
    
    @staticmethod
    def find_mac_on_network(target_mac: str) -> Optional[Dict[str, str]]:
        """Find a specific MAC address on the local network"""
        print(f" Searching for MAC address {target_mac} on local network...")
    
    @staticmethod
    def find_mac_by_ip(target_ip: str) -> Optional[str]:
        """Attempt to resolve MAC address for a given IPv4 via ARP (ping first if needed)."""
        try:
            # First try ARP table directly
            entries = NetworkScanner.scan_arp_table()
            for e in entries:
                if e.get('ip') == target_ip:
                    return e.get('mac')
            # Ping target to populate ARP
            subprocess.run(['ping', '-c', '1', '-W', '1000', target_ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            entries = NetworkScanner.scan_arp_table()
            for e in entries:
                if e.get('ip') == target_ip:
                    return e.get('mac')
        except Exception:
            return None
        return None
        
        # First check ARP table
        entries = NetworkScanner.scan_arp_table(target_mac)
        if entries:
            entry = entries[0]
            print(f" Found MAC {target_mac} at IP {entry['ip']} (hostname: {entry['hostname']})")
            return entry
        
        print(f" MAC {target_mac} not in current ARP table, trying network sweep...")
        
        # If not found, do network sweep and try again
        network_range = NetworkScanner.get_local_network_range()
        if network_range:
            NetworkScanner.ping_sweep_network(network_range)
            
            # Check ARP table again after sweep
            entries = NetworkScanner.scan_arp_table(target_mac)
            if entries:
                entry = entries[0]
                print(f" Found MAC {target_mac} at IP {entry['ip']} after network sweep")
                return entry
        
        print(f" MAC address {target_mac} not found on local network")
        print(f"   This could mean:")
        print(f"   - VM is stopped or not responding to network traffic")
        print(f"   - VM is on a different network segment")
        print(f"   - MAC address in Proxmox config doesn't match actual VM")
        return None

class WakeOnLan:
    """Wake-on-LAN functionality"""
    
    @staticmethod
    def send_wol_packet(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> bool:
        """Send Wake-on-LAN magic packet"""
        try:
            # Remove any separators from MAC address
            mac_address = mac_address.replace(':', '').replace('-', '').replace('.', '')
            
            if len(mac_address) != 12:
                raise ValueError("MAC address must be 12 hex characters")
            
            # Convert MAC address to bytes
            mac_bytes = bytes.fromhex(mac_address)
            
            # Create magic packet: 6 bytes of 0xFF followed by 16 repetitions of MAC address
            magic_packet = b'\xff' * 6 + mac_bytes * 16
            
            # Send packet
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic_packet, (broadcast_ip, port))
            sock.close()
            
            print(f"WoL packet sent to {mac_address} via {broadcast_ip}:{port}")
            return True
            
        except Exception as e:
            print(f"Failed to send WoL packet: {e}")
            return False
    
    @staticmethod
    def validate_mac_address(mac_address: str) -> bool:
        """Validate MAC address format"""
        # Remove separators
        clean_mac = mac_address.replace(':', '').replace('-', '').replace('.', '')
        
        # Check if it's 12 hex characters
        if len(clean_mac) != 12:
            return False
        
        try:
            int(clean_mac, 16)
            return True
        except ValueError:
            return False

def interactive_add_vm(auto_approve: bool = False, start_external: bool = False):
    """Interactive function to add a Proxmox VM or external host to Guacamole.

    start_external: skip Proxmox listing and immediately configure an external host.
    """
    config = Config()
    guac_api = GuacamoleAPI(config)
    proxmox_api = ProxmoxAPI(config)
    
    # Initialize variables
    selected_hostname: Optional[str] = None
    
    console.print(Panel.fit("[bold]Add Connection to Guacamole[/bold]", border_style="cyan"))
    
    # Authenticate with Guacamole
    if not guac_api.authenticate():
        print("Failed to authenticate with Guacamole")
        return False
    
    # Get VMs from Proxmox (skip if starting with external)
    vms = []
    if not start_external:
        # Authenticate with Proxmox first (consistent success panel)
        if not proxmox_api.test_auth():
            return False
        console.print("\n[cyan]Fetching VMs from Proxmox...[/cyan]")
        try:
            from rich.progress import Progress, SpinnerColumn, TextColumn
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                task = progress.add_task("Loading VM list...", total=None)
                vms = proxmox_api.get_vms()
                progress.update(task, completed=True)
        except Exception:
            # Fallback without progress if Rich progress fails for any reason
            vms = proxmox_api.get_vms()
    
    if vms and not start_external:
            # Get existing Guacamole connections to check which VMs are already configured
            existing_connections = guac_api.get_connections()
            existing_connection_names = set()
            if existing_connections:
                for conn_id, conn in existing_connections.items():
                    existing_connection_names.add(conn.get('name', ''))
        
            # Categorize VMs: those with credentials and unconfigured vs others
            vms_with_unconfigured_creds = []
            vms_with_configured_creds = []
            vms_without_creds = []
        
            for vm in vms:
                vm_id = vm.get('vmid')
                vm_name = vm.get('name', '')
                node_name = vm.get('node')
            
                # Skip if essential VM info is missing
                if not vm_id or not node_name or not isinstance(vm_id, int):
                    vms_without_creds.append(vm)
                    continue
                
                # Check if VM has credentials in notes
                try:
                    vm_config = proxmox_api.get_vm_config(node_name, vm_id)
                    notes = vm_config.get('description', '')
                    # Capture memory for later display (try common keys)
                    vm_mem = vm_config.get('memory') or vm_config.get('maxmem') or vm.get('maxmem') or vm.get('mem')
                    if vm_mem is not None:
                        vm['_memory'] = vm_mem
                
                    if notes:
                        parsed_creds = proxmox_api.parse_credentials_from_notes(
                            notes, vm_name, str(vm_id), node_name
                        )
                    
                        if parsed_creds:
                            # Store parsed creds on VM for later use
                            vm['_parsed_creds'] = parsed_creds

                            # Determine configured status for this VM by comparing parsed creds
                            # against existing Guacamole connections. Possible values:
                            #  - "" (empty): not configured
                            #  - "Done": configured and in sync
                            #  - "out of sync": configured but settings differ
                            configured_status = ""
                            try:
                                sync_issues = []
                                for cred in parsed_creds:
                                    conn_name = cred.get('connection_name')
                                    if not conn_name:
                                        continue
                                    existing = guac_api.get_connection_by_name(conn_name)
                                    if not existing:
                                        configured_status = ""  # Missing one -> treat as not configured
                                        sync_issues.append(f"Missing connection: {conn_name}")
                                        break
                                    details = guac_api.get_connection_details(existing['identifier'])
                                    params = details.get('parameters', {})
                                    # Collect mismatches
                                    if params.get('username') != cred.get('username'):
                                        sync_issues.append(f"{conn_name}: username differs (Guac='{params.get('username')}' vs Notes='{cred.get('username')}')")
                                    if params.get('port') != str(cred.get('port', '')):
                                        sync_issues.append(f"{conn_name}: port differs (Guac='{params.get('port')}' vs Notes='{cred.get('port')}')")
                                    existing_proto = (details.get('protocol') or existing.get('protocol') or '').lower()
                                    if existing_proto and existing_proto != cred.get('protocol', '').lower():
                                        sync_issues.append(f"{conn_name}: protocol differs (Guac='{existing_proto}' vs Notes='{cred.get('protocol')}' )")
                                if sync_issues:
                                    configured_status = "out of sync"
                                    vm['_sync_issues'] = sync_issues
                                else:
                                    # Only mark Done if every parsed credential had a matching connection with no mismatches
                                    configured_status = "Done"
                            except Exception:
                                configured_status = configured_status or ""
                            vm['_configured_status'] = configured_status
                            # Check if any connection from this VM already exists
                            has_existing_connections = any(
                                cred.get('connection_name') in existing_connection_names
                                for cred in parsed_creds
                            )
                        
                            if has_existing_connections:
                                vms_with_configured_creds.append(vm)
                            else:
                                vms_with_unconfigured_creds.append(vm)
                        else:
                            vms_without_creds.append(vm)
                    else:
                        vms_without_creds.append(vm)
                except Exception:
                    vms_without_creds.append(vm)
        
            # Combine VMs in priority order: unconfigured with creds first, then configured, then without creds
            prioritized_vms = vms_with_unconfigured_creds + vms_with_configured_creds + vms_without_creds
        
            console.print(f"\n[bold]Found {len(vms)} VMs in Proxmox:[/bold]")
            if vms_with_unconfigured_creds:
                console.print(f"[green]* {len(vms_with_unconfigured_creds)} VMs ready for setup (have credentials in notes)[/green]")
            if vms_with_configured_creds:
                console.print(f"[yellow]✔ {len(vms_with_configured_creds)} VMs already configured[/yellow]")
        
            from rich.table import Table  # local import to avoid import ordering issues
            table = Table(title=f" Proxmox VMs ({len(prioritized_vms)} found)")
            table.add_column("#", style="bold", no_wrap=True, width=4)
            table.add_column("ID", style="cyan", no_wrap=True, width=6)
            table.add_column("Name", style="magenta", min_width=18)
            table.add_column("Node", style="cyan", no_wrap=True, width=8)
            table.add_column("Status", style="bold", no_wrap=True, width=12)
            table.add_column("Configured", style="bold", no_wrap=True, width=12)
            table.add_column("Memory", style="bold", no_wrap=True, width=10)

            def _format_memory(val):
                try:
                    num = int(val)
                except Exception:
                    return str(val)

                # If value looks like bytes, convert to MiB/GiB; otherwise keep
                # Assume value in bytes if > 1024
                if num >= 1024:
                    mib = num / 1024.0 / 1024.0
                    if mib >= 1024:
                        return f"{mib/1024.0:.1f}GiB"
                    return f"{mib:.0f}MiB"
                return f"{num}B"

            for idx, vm in enumerate(prioritized_vms, start=1):
                status = vm.get('status', 'N/A')
                if status == 'running':
                    status_icon = "[green]●[/green] running"
                elif status == 'stopped':
                    status_icon = "[yellow]○[/yellow] stopped"
                else:
                    status_icon = f"[red]{status}[/red]"

                # Determine configured status ('' / Done / out of sync)
                cfg = vm.get('_configured_status', '')
                if cfg == 'Done':
                    configured_display = "[green]Done[/green]"
                elif cfg == 'out of sync':
                    configured_display = "[red]Out of sync[/red]"
                else:
                    # If VM has credentials but none exist in Guacamole, show empty (user will see "ready" note above)
                    configured_display = ""

                # VM name fallback: try name, then hostname, then vmid
                vm_name = vm.get('name') or vm.get('hostname') or str(vm.get('vmid', 'N/A'))

                mem_display = ""  # default blank
                mem_val = vm.get('_memory')
                if mem_val is not None:
                    mem_display = _format_memory(mem_val)
                table.add_row(str(idx), str(vm.get('vmid', 'N/A')), vm_name, vm.get('node', ''), status_icon, configured_display, mem_display)

            console.print(table)

            # Always show sync issue details if present
            any_sync = [vm for vm in vms if vm.get('_sync_issues')]
            if any_sync:
                console.print("\n[red]Out-of-sync details:[/red]")
                for vm in any_sync:
                    name = vm.get('name') or vm.get('vmid')
                    for issue in vm.get('_sync_issues', []):
                        console.print(f"  - [cyan]{name}[/cyan] -> {issue}")
        
            # Update vms to use the prioritized order
            vms = prioritized_vms
    elif not start_external:
        print("Warning: No VMs found in Proxmox. This could mean:")
        print("  - No VMs are created yet (create them in Proxmox web interface)")
        print("  - Token lacks VM listing permissions")
        print("  - VMs exist on different nodes in a cluster")
        print()
        manual_choice = input("Continue with manual VM entry? (y/n) [y]: ").strip().lower()
        if manual_choice and manual_choice not in ("y", "yes"):
            return False
        
        # Create a fake VM entry for manual mode
        vms = [{"vmid": "manual", "name": "Manual Entry", "node": "manual", "status": "manual"}]
    
    if not start_external:
        console.print("\n" + "-" * 50)
        console.print("[bold cyan]Options:[/bold cyan]")
        console.print("  • Select VM by ID, name, or number from list above")
        console.print("  • Type 'external' for non-Proxmox host")
    
    selected_vm = None
    is_external_host = False
    vm_lookup_by_id = {str(vm.get('vmid')): vm for vm in vms}
    vm_lookup_by_name = {vm.get('name', '').lower(): vm for vm in vms if vm.get('name')}
    detected_mac: Optional[str] = None  # ensure symbol exists for external host flow

    while not selected_vm:
        if start_external:
            is_external_host = True
            selected_vm = {"vmid": "external", "name": "External Host", "node": "external", "status": "external"}
            break
        selection = input("Selection: ").strip()
        if not selection:
            print("A selection is required")
            continue

        if selection.lower() in ('external', 'ext', 'e'):
            is_external_host = True
            selected_vm = {"vmid": "external", "name": "External Host", "node": "external", "status": "external"}
            break

        # Try by VMID exact match
        if selection in vm_lookup_by_id:
            selected_vm = vm_lookup_by_id[selection]
            break

        # Try numeric index
        if selection.isdigit():
            index = int(selection) - 1
            if 0 <= index < len(vms):
                selected_vm = vms[index]
                break

        # Try name lookup
        name_key = selection.lower()
        if name_key in vm_lookup_by_name:
            selected_vm = vm_lookup_by_name[name_key]
            break

        print("Could not find a VM matching that input. Please try again.")

    if is_external_host:
        # Handle external host configuration
        print("\n External Host Configuration")
        print("=" * 40)
        
        host_name = input("Host name/description: ").strip()
        if not host_name:
            host_name = "External Host"
            
        selected_hostname = input("Hostname/IP address: ").strip()
        if not selected_hostname:
            print("Hostname/IP is required for external hosts")
            return False
            
        # External hosts have no Proxmox integration
        vm_name = host_name
        vm_node = None
        vm_id = None
        original_status = "external"
        parsed_credentials = []
        vm_macs = []
        network_details = []
        vm_notes = ""
        vm_was_started = False
        
        print(f"\nExternal Host: {host_name} ({selected_hostname})")
        # Attempt passive MAC detection for external host
        detected_mac = None
        if re.match(r'^\d+\.\d+\.\d+\.\d+$', selected_hostname):  # simple IPv4 check
            detected_mac = NetworkScanner.find_mac_by_ip(selected_hostname)
            if detected_mac:
                print(f" Detected MAC via ARP: {detected_mac}")
        # Skip later IP discovery / guidance entirely for external hosts

        # Extended network + port scanning suggestions (external only)
        try:
            default_ports = {"ssh": 22, "rdp": 3389, "vnc": 5900}
            network_range = NetworkScanner.get_local_network_range()
            suggested: List[Tuple[str, List[str]]] = []  # (ip, [proto,...])
            if network_range:
                print(f"\nDiscovering active hosts on {network_range} (parallel ping sweep)...")
                # Perform broader ping sweep (reuse existing but maybe widen host count)
                try:
                    NetworkScanner.ping_sweep_network(network_range)
                except Exception:
                    pass
                # Collect ARP discovered IPs
                arp_entries = NetworkScanner.scan_arp_table()
                ips = [e['ip'] for e in arp_entries]
                if ips:
                    print(f" Found {len(ips)} ARP entries. Scanning default service ports (22,3389,5900)...")
                    # Simple multithreaded port scan (fast timeout)
                    import socket
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    def check(ip: str, proto: str, port: int) -> Optional[Tuple[str, str]]:
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                                s.settimeout(0.35)
                                if s.connect_ex((ip, port)) == 0:
                                    return (ip, proto)
                        except Exception:
                            return None
                        return None
                    combos = []
                    for ip in ips:
                        for proto, port in default_ports.items():
                            combos.append((ip, proto, port))
                    found: Dict[str, List[str]] = {}
                    max_workers = min(64, len(combos)) or 1
                    with ThreadPoolExecutor(max_workers=max_workers) as ex:
                        futs = [ex.submit(check, ip, proto, port) for ip, proto, port in combos]
                        for fut in as_completed(futs):
                            res = fut.result()
                            if res:
                                ip, proto = res
                                found.setdefault(ip, []).append(proto)
                    for ip, protos in found.items():
                        suggested.append((ip, sorted(set(protos))))
                if suggested:
                    print("\nSuggested external hosts (detected open default ports):")
                    for ip, protos in suggested[:15]:
                        print(f"  {ip} -> {', '.join(protos)}")
                    print("(Select one of these IPs above if it matches your target; this list is not exhaustive.)")
            else:
                print("Could not determine local network range; skipping external host discovery suggestions.")
        except Exception as e:
            print(f"Warning: External host suggestion scan failed: {e}")
        
    else:
        # Handle Proxmox VM configuration
        vm_name = selected_vm.get('name', f"VM-{selected_vm.get('vmid')}")
        vm_node = selected_vm.get('node')
        if not vm_node:
            vm_node = input("Proxmox node for this VM (e.g., pve): ").strip()
            if not vm_node:
                print("Unable to determine node for VM")
                return False

        vm_id_value = selected_vm.get('vmid')
        if vm_id_value is None:
            while True:
                try:
                    vm_id_value = int(input("Enter VMID: ").strip())
                    break
                except ValueError:
                    print("Please provide a numeric VMID")
        vm_id = int(vm_id_value)

        console.print(f"\n[bold]Selected VM:[/bold] [cyan]{vm_name}[/cyan] (ID: [yellow]{vm_id}[/yellow], Node: [green]{vm_node}[/green])")

        # Check VM status
        vm_status = proxmox_api.get_vm_status(vm_node, vm_id)
        original_status = vm_status.get('status', 'unknown')
        console.print(f"VM Status: [magenta]{original_status}[/magenta]")
        
        # Get VM notes for credential parsing
        vm_notes = proxmox_api.get_vm_notes(vm_node, vm_id)
        parsed_credentials = proxmox_api.parse_credentials_from_notes(vm_notes, vm_name, str(vm_id), vm_node, "unknown")
        
        if parsed_credentials:
            console.print(f"\n[green] Found {len(parsed_credentials)} credential set(s) in VM notes:[/green]")
            for i, cred in enumerate(parsed_credentials, 1):
                console.print(f"  {i}. [cyan]{cred['username']}[/cyan] ([magenta]{cred['protocol']}[/magenta]) - [yellow]{cred['connection_name']}[/yellow]")
            # Offer immediate action before proceeding
            if not auto_approve:
                while True:
                    choice = input("Apply credentials from notes? (a=apply / i=ignore / e=edit) [a]: ").strip().lower()
                    if choice in ("", "a", "apply"):
                        break  # keep as-is
                    if choice in ("i", "ignore"):
                        parsed_credentials = []
                        break
                    if choice in ("e", "edit"):
                        try:
                            index = input("Enter number of credential to edit (or blank to finish): ").strip()
                            if index and index.isdigit():
                                idx = int(index) - 1
                                if 0 <= idx < len(parsed_credentials):
                                    cred = parsed_credentials[idx]
                                    new_user = input(f"Username [{cred['username']}]: ").strip() or cred['username']
                                    new_proto = input(f"Protocol (rdp/vnc/ssh) [{cred['protocol']}]: ").strip().lower() or cred['protocol']
                                    if new_proto not in ("rdp", "vnc", "ssh"):
                                        print("Invalid protocol - keeping original")
                                        new_proto = cred['protocol']
                                    try:
                                        new_port_raw = input(f"Port [{cred.get('port')}]: ").strip()
                                        new_port = int(new_port_raw) if new_port_raw else cred.get('port')
                                    except ValueError:
                                        print("Invalid port - keeping original")
                                        new_port = cred.get('port')
                                    new_name = input(f"Connection name [{cred['connection_name']}]: ").strip() or cred['connection_name']
                                    cred['username'] = new_user
                                    cred['protocol'] = new_proto
                                    try:
                                        if new_port is not None:
                                            cred['port'] = str(int(new_port))
                                    except Exception:
                                        # Leave as original on failure
                                        pass
                                    cred['connection_name'] = new_name
                                    print("Updated credential.")
                                else:
                                    print("Index out of range")
                            else:
                                break
                        except Exception as e:
                            print(f"Edit error: {e}")
                        continue
                    else:
                        print("Please choose a / i / e")
                        continue
        else:
            console.print("\n[yellow]Warning: No credentials found in VM notes[/yellow]")

    # Network processing only for Proxmox VMs
    if not is_external_host:
        # vm_node can be None or other types in some code paths; ensure it's a string
        if vm_node is None:
            print("Unable to determine Proxmox node for this VM; skipping network discovery")
            network_details = []
        else:
            # Cast/ensure type for static checkers (Pylance) and runtime safety
            vm_node_str: str = str(vm_node)
            # vm_id may sometimes be None (manual entries); ensure it's an int before calling
            if vm_id is None:
                print("VM ID is not available; skipping Proxmox network discovery")
                network_details = []
            else:
                try:
                    vm_id_int: int = int(vm_id)
                except (ValueError, TypeError):
                    print(f"Invalid VM ID '{vm_id}'; skipping network discovery")
                    network_details = []
                else:
                    network_details = proxmox_api.get_vm_network_info(vm_node_str, vm_id_int)
        
        # Get all MACs from network interfaces
        vm_macs = []
        if network_details:
            for interface in network_details:
                # Check multiple possible MAC fields
                mac = (interface.get('mac') or 
                       interface.get('virtio') or 
                       interface.get('e1000') or 
                       interface.get('rtl8139'))
                if mac:
                    vm_macs.append(mac)
    else:
        # External hosts have no network details from Proxmox
        vm_macs = []
        network_details = []
    
    # Try to find any of the VM's MACs on the network (Proxmox VMs only)
    network_scan_result = None
    found_mac = None
    vm_was_started = False
    
    if vm_macs:
        print(f"\n Found VM network adapter MAC(s): {', '.join(vm_macs)}")
        
        # Try each MAC until we find one on the network
        for mac in vm_macs:
            network_scan_result = NetworkScanner.find_mac_on_network(mac)
            if network_scan_result:
                found_mac = mac
                print(f"Found MAC {mac} on network at IP {network_scan_result['ip']}")
                break
        
        if not network_scan_result:
            print("None of the VM's MACs found on network")
            found_mac = vm_macs[0]  # Use first MAC as fallback
        
        # Check VM state and automatically start if needed (Proxmox VMs only)
        if original_status in ('stopped', 'shutdown') and not is_external_host:
            if auto_approve:
                start_choice = "y"
                print(f"\n VM is {original_status}. Auto-starting VM for connection setup...")
            else:
                start_choice = input(f"\n VM is {original_status}. Start VM for connection setup? (y/n) [y]: ").strip().lower()
            
            if start_choice == "" or start_choice in ("y", "yes"):
                if vm_node and vm_id and proxmox_api.start_vm(vm_node, vm_id):
                    vm_was_started = True
                    print(" Waiting 30 seconds for VM to boot and connect to network...")
                    time.sleep(30)
                    
                    # Try network scan again with all MACs
                    print(" Scanning for VM on network after startup...")
                    for mac in vm_macs:
                        network_scan_result = NetworkScanner.find_mac_on_network(mac)
                        if network_scan_result:
                            found_mac = mac
                            print(f" Found MAC {mac} on network at IP {network_scan_result['ip']} after startup")
                            break
                    
                    if not network_scan_result:
                        print("  VM started but not yet detected on network (may need more time to boot)")
        elif not network_scan_result and not is_external_host:
            # VM is running but not found on network - this might be normal for some network configs
            print(f"  VM is {original_status} but not detected on network")
            print("    This could be normal if VM has no qemu-guest-agent or different network config")
    # Initialize variables
    selected_mac = None
    
    # Skip IP discovery for external hosts (already have hostname)  
    if is_external_host:
        ip_options: List[Dict] = []
        mac_candidates: List[Dict] = []
        # selected_hostname is already set for external hosts in the external host section above
    else:
        # IP discovery for Proxmox VMs
        ip_options: List[Dict] = []
        mac_candidates: List[Dict] = []

        # First priority: Guest agent IPs (from running VM)
        guest_agent_ips = []
        for interface in network_details:
            mac = interface.get('mac')
            if mac:
                existing = next((item for item in mac_candidates if item['mac'].lower() == mac.lower()), None)
                if not existing:
                    mac_candidates.append({
                        'mac': mac,
                        'interface': interface.get('guest_interface') or interface.get('interface')
                    })

            # Collect guest agent IPs (these have highest priority)
            for addr in interface.get('ip_addresses', []):
                ip_addr = addr.get('ip-address') or addr.get('address')
                if not ip_addr:
                    continue
                # Skip loopback, link-local, and IPv6 addresses  
                if ip_addr.startswith('127.') or ip_addr.startswith('169.254.') or ip_addr.startswith('::1') or ip_addr.startswith('fe80:'):
                    continue
                # Skip all IPv6 addresses
                if '::' in ip_addr or (':' in ip_addr and '.' not in ip_addr):
                    continue
                    
                label = ip_addr
                if addr.get('prefix') is not None:
                    label += f"/{addr['prefix']}"
                iface_name = interface.get('guest_interface') or interface.get('interface') or "unknown"
                
                guest_agent_ip = {
                    'label': f"{label} (guest agent: {iface_name})",
                    'address': ip_addr,
                    'interface': iface_name,
                    'mac': mac,
                    'source': 'guest_agent'
                }
                guest_agent_ips.append(guest_agent_ip)
                ip_options.append(guest_agent_ip)
        
        if guest_agent_ips:
            console.print(f"[green] Found {len(guest_agent_ips)} IP(s) from guest agent (highest priority)[/green]")
        else:
            console.print("[yellow]  No IPs found from guest agent[/yellow]")
            console.print("    To enable: install qemu-guest-agent in VM and restart")

        if not is_external_host:
            selected_hostname = None
        if network_scan_result:
            scanned_ip = network_scan_result['ip']
            # Check if this IP is already in the options from guest agent
            if not any(opt['address'] == scanned_ip for opt in ip_options):
                # Add to end of list (lower priority than guest agent)
                scanned_option = {
                    'label': f"{scanned_ip} (network scan)",
                    'address': scanned_ip,
                    'interface': 'network-scan',
                    'mac': found_mac,
                    'source': 'network_scan'
                }
                ip_options.append(scanned_option)
                print(f" Added network-scanned IP: {scanned_ip}")
            else:
                print(f" Network scan confirmed existing IP: {scanned_ip}")

    # Handle IP selection
    if not is_external_host:
        selected_hostname = None
    
    if ip_options:
        # Reorder to prefer IPv4 addresses first while keeping relative ordering inside families
        try:
            ipv4_opts = [o for o in ip_options if ':' not in o.get('address','')]
            ipv6_opts = [o for o in ip_options if ':' in o.get('address','')]
            if ipv4_opts and ipv6_opts:
                # Preserve original order inside each subset
                orig_index = {id(o): i for i, o in enumerate(ip_options)}
                ipv4_opts.sort(key=lambda o: orig_index[id(o)])
                ipv6_opts.sort(key=lambda o: orig_index[id(o)])
                ip_options = ipv4_opts + ipv6_opts
        except Exception:
            pass
        console.print("\n[bold]Discovered IP addresses:[/bold]")
        for idx, option in enumerate(ip_options, start=1):
            source_icon = "●" if option.get('source') == 'guest_agent' else "○" if option.get('source') == 'network_scan' else ""
            console.print(f"  {idx}. {source_icon} [green]{option['label']}[/green]")
        if not auto_approve:
            console.print("  m.  Enter manually")

        chosen: Optional[Dict] = None
        if auto_approve:
            # Auto pick first IPv4 if present
            chosen = next((o for o in ip_options if ':' not in o.get('address','')), ip_options[0])
            print(f"Auto-selected (IPv4 preference): {chosen['label']}")
        else:
            while True:
                ip_choice = input("Choose IP for Guacamole connection [1]: ").strip().lower()
                if ip_choice == "" or ip_choice == "1":
                    chosen = ip_options[0]
                    break
                if ip_choice == "m":
                    manual_ip = input("Enter IP address or hostname: ").strip()
                    if manual_ip:
                        selected_hostname = manual_ip
                        break
                    else:
                        print("Hostname cannot be empty")
                        continue
                if ip_choice.isdigit():
                    idx = int(ip_choice) - 1
                    if 0 <= idx < len(ip_options):
                        chosen = ip_options[idx]
                        break
                print("Invalid choice. Please select from the list or 'm' for manual entry.")

        if selected_hostname is None and chosen is not None:
            selected_hostname = chosen['address']
            selected_mac = chosen.get('mac')
            console.print(f"[cyan]Selected IP:[/cyan] [green]{selected_hostname}[/green]")
            
        # Update parsed credentials with actual IP if we have it (Proxmox VMs only)
        if selected_hostname and selected_hostname != "unknown" and parsed_credentials and not is_external_host:
            if vm_id is not None and vm_node:
                parsed_credentials = proxmox_api.parse_credentials_from_notes(vm_notes, vm_name, str(vm_id), vm_node, selected_hostname)
    else:
        if not is_external_host:
            # No IP options found - provide helpful guidance (Proxmox only)
            print(f"\n  No IP addresses could be automatically detected for {vm_name}")
            print("   This is likely because:")
            print("   • Guest agent is not installed/running (install qemu-guest-agent)")
            print("   • VM is stopped or not network accessible")
            print("   • VM is on a different network segment")
            print()
            
            while True:
                manual_ip = input("Enter VM IP address/hostname: ").strip()
                if manual_ip:
                    selected_hostname = manual_ip
                    break
                print("Hostname is required to create connection")
        else:
            # External host path: keep original entered hostname
            pass

    if not selected_hostname:
        console.print("[red]Unable to determine hostname for the connection.[/red]")
        return False
    selected_hostname = str(selected_hostname)

    # Prefer ARP/MAC detection order (detected_mac set in external branch if applicable)

    if found_mac:
        selected_mac = found_mac
        print(f"\nUsing network-discovered MAC: {found_mac}")
    elif not selected_mac and mac_candidates:
        selected_mac = mac_candidates[0]['mac']
    elif is_external_host and detected_mac and not selected_mac:
        selected_mac = detected_mac
        print(f"Using detected external host MAC: {selected_mac}")

    if mac_candidates and not auto_approve:
        console.print("\n[bold]Available MAC addresses:[/bold]")
        for idx, option in enumerate(mac_candidates, start=1):
            label = option['mac']
            if option.get('interface'):
                label += f" (iface: {option['interface']})"
            # Mark the preferred MAC
            if option['mac'] == selected_mac:
                if option['mac'] == found_mac:
                    label += " (network-discovered, default)"
                else:
                    label += " (default)"
            console.print(f"  {idx}. [yellow]{label}[/yellow]")
        console.print("  m. Enter manually")

        while True:
            mac_choice = input("Choose MAC for Wake-on-LAN [1]: ").strip().lower()
            if mac_choice == "" or mac_choice == "1":
                selected_mac = mac_candidates[0]['mac']
                break
            if mac_choice == "m":
                manual_mac = input("Enter MAC address (e.g., 52:54:00:12:34:56): ").strip()
                if WakeOnLan.validate_mac_address(manual_mac):
                    selected_mac = manual_mac
                    break
                else:
                    print("Invalid MAC address format")
                    continue
            if mac_choice.isdigit():
                idx = int(mac_choice) - 1
                if 0 <= idx < len(mac_candidates):
                    selected_mac = mac_candidates[idx]['mac']
                    break
            print("Invalid choice. Please select from the list or 'm' for manual entry.")

    # Allow users to override hostname even after selection
    if auto_approve:
        print(f"Using hostname: {selected_hostname}")
    else:
        hostname_override = input(f"Hostname for connections [{selected_hostname}]: ").strip()
        if hostname_override:
            selected_hostname = hostname_override

    # Skip protocol selection in auto-approve mode - protocols must come from VM notes
    default_protocol = None
    default_port = None
    if not auto_approve:
        dp = input("Default protocol for connections (rdp/vnc/ssh) [leave blank to set per-account]: ").strip().lower()
        if dp:
            if dp not in ("rdp", "vnc", "ssh"):
                console.print("[yellow]Warning: Invalid protocol. Protocols must be specified per account.[/yellow]")
                dp = None
        default_protocol = dp or None

        if default_protocol == "rdp":
            default_port = config.DEFAULT_RDP_PORT
        elif default_protocol == "ssh":
            default_port = 22
        elif default_protocol == "vnc":
            default_port = config.DEFAULT_VNC_PORT

        if default_port is not None:
            proto_label = default_protocol.upper() if default_protocol else ""
            port_input = input(f"Default port for {proto_label} connections [{default_port}]: ").strip()
            if port_input:
                try:
                    default_port = int(port_input)
                except ValueError:
                    console.print("[yellow]Warning: Invalid port specified. Using default.[/yellow]")
    else:
        console.print("[yellow]Auto-approve mode: Protocols and settings must be specified in VM notes[/yellow]")

# Connection count is now determined by parsed credentials or manual entry

    enable_wol = False
    if selected_mac:
        if auto_approve:
            enable_wol = True
            print(f"Wake-on-LAN enabled with MAC: {selected_mac}")
        else:
            wol_choice = input("Enable Wake-on-LAN for these connections? (y/n) [y]: ").strip().lower()
            if wol_choice == "" or wol_choice in ("y", "yes"):
                enable_wol = True
    else:
        if auto_approve:
            print("Warning: No MAC detected. Wake-on-LAN will be disabled.")
        else:
            wol_choice = input("No MAC detected. Provide one to enable Wake-on-LAN? (y/n) [n]: ").strip().lower()
            if wol_choice in ("y", "yes"):
                while True:
                    manual_mac = input("Enter MAC address (e.g., 52:54:00:12:34:56): ").strip()
                    if WakeOnLan.validate_mac_address(manual_mac):
                        selected_mac = manual_mac
                        enable_wol = True
                        break
                    print("Invalid MAC address format")

    connections_to_create: List[Dict] = []

    # Use parsed credentials if available, otherwise prompt for manual entry
    if parsed_credentials:
        print(f"\nUsing {len(parsed_credentials)} credential set(s) from VM notes")
        for i, cred in enumerate(parsed_credentials):
            protocol = cred['protocol']
            port_value = cred.get('port', config.DEFAULT_RDP_PORT if protocol == "rdp" else (22 if protocol == "ssh" else config.DEFAULT_VNC_PORT))
            
            connections_to_create.append({
                'name': cred['connection_name'],
                'username': cred['username'],
                'password': cred['password'],
                'protocol': protocol,
                'port': port_value,
                'rdp_settings': cred.get('rdp_settings'),
                'wol_settings': cred.get('wol_settings'),
                'wol_disabled': cred.get('wol_disabled', False)
            })
            print(f"  {i+1}. {cred['connection_name']} ({cred['username']}, {protocol}:{port_value})")
        
        if not auto_approve and parsed_credentials:
            # Old confirm replaced by earlier apply prompt; keep backward compat if needed
            pass
    
    # Manual credential entry if no parsed credentials or user declined
    if not parsed_credentials:
        if auto_approve:
            print("\nWarning: No credentials in VM notes and auto-approve mode enabled.")
            print("Please add credentials to VM notes or disable auto-approve mode.")
            return False
        
        # Multiple user support - keep adding users until they say no
        connection_index = 0
        while True:
            connection_index += 1
            print("\n" + "-" * 50)
            print(f"Account {connection_index}")
            print("-" * 50)

            username = input("Username: ").strip()
            password = getpass.getpass("Password: ").strip()

            # Protocol prompt: use default_protocol as fallback when left blank
            if default_protocol:
                protocol_prompt = f"Protocol for this connection (rdp/vnc/ssh) [{default_protocol}]: "
            else:
                protocol_prompt = "Protocol for this connection (rdp/vnc/ssh): "

            protocol = input(protocol_prompt).strip().lower()
            if protocol == "" and default_protocol:
                protocol = default_protocol

            if protocol not in ("rdp", "vnc", "ssh"):
                print("Error: Please specify a valid protocol (rdp/vnc/ssh)")
                continue

            # Port default: if this protocol matches the global default_protocol and default_port is set, use that
            if default_port is not None and default_protocol == protocol:
                port_value = default_port
            else:
                if protocol == "rdp":
                    port_value = config.DEFAULT_RDP_PORT
                elif protocol == "ssh":
                    port_value = 22
                else:  # vnc
                    port_value = config.DEFAULT_VNC_PORT

            port_override = input(f"Port for {protocol.upper()} connection [{port_value}]: ").strip()
            if port_override:
                try:
                    port_value = int(port_override)
                except ValueError:
                    console.print("[yellow]Warning: Invalid port. Using default for this connection.[/yellow]")

            suggested_name = f"{vm_name}-{username}" if username else f"{vm_name}-conn{connection_index}"
            connection_name = input(f"Connection name [{suggested_name}]: ").strip()
            if not connection_name:
                connection_name = suggested_name

            connections_to_create.append({
                'name': connection_name,
                'username': username,
                'password': password,
                'protocol': protocol,
                'port': port_value,
                'rdp_settings': None,
                'wol_settings': None,
                'wol_disabled': False
            })
            
            # Ask if user wants to add another connection
            another_user = input(f"\nDo you want to set up another connection for this {'VM' if not is_external_host else 'computer'}? (y/n) [n]: ").strip().lower()
            if another_user not in ("y", "yes"):
                break

    parent_identifier = None
    # Create a connection group only if there are multiple connections (for both VMs and external hosts)
    if len(connections_to_create) > 1:
        if auto_approve:
            group_name = vm_name
            console.print(f"[cyan]Creating connection group: {group_name}[/cyan]")
            parent_identifier = guac_api.create_connection_group(group_name)
            if parent_identifier is None:
                console.print("[yellow]Warning: Failed to create connection group. Connections will be created at root level.[/yellow]")
        else:
            connection_type = "host" if is_external_host else "VM"
            group_choice = input(f"Create a connection group for {connection_type} connections? (y/n) [y]: ").strip().lower()
            if group_choice == "" or group_choice in ("y", "yes"):
                default_group_name = vm_name
                group_name = input(f"Group name [{default_group_name}]: ").strip()
                if not group_name:
                    group_name = default_group_name
                parent_identifier = guac_api.create_connection_group(group_name)
                if parent_identifier is None:
                    console.print("[yellow]Warning: Failed to create connection group. Connections will be created at root level.[/yellow]")

    # Check for duplicates/existing connections that might need updates
    duplicates = []
    updates_needed = []
    unique_connections = []
    
    for conn in connections_to_create:
        # First check if connection exists in the target parent location
        existing_conn = guac_api.get_connection_by_name_and_parent(conn['name'], parent_identifier)
        
        if existing_conn:
            # Connection exists in target location - check if it needs updating
            params = existing_conn.get('parameters', {})
            needs_update = (
                params.get('hostname') != selected_hostname or
                params.get('username') != conn['username'] or
                params.get('password') != conn['password'] or
                params.get('port') != str(conn['port'])
            )
            
            if needs_update:
                updates_needed.append((conn, existing_conn['identifier']))
            else:
                duplicates.append(conn['name'])
        else:
            # Check if connection exists in a different parent location
            any_existing_conn = guac_api.get_connection_by_name(conn['name'])
            if any_existing_conn:
                # Connection exists but in wrong location - need to update its parent
                print(f"Warning: Found connection '{conn['name']}' in different location - will update to use group")
                updates_needed.append((conn, any_existing_conn['identifier']))
            else:
                # Connection doesn't exist anywhere - create new
                unique_connections.append(conn)
    
    # Handle updates for existing connections
    if updates_needed:
        print(f"\nFound {len(updates_needed)} connection(s) that need updating:")
        for conn, identifier in updates_needed:
            print(f"  - {conn['name']} (password/settings changed)")
        
        if not auto_approve:
            update_choice = input("\nAction for existing connections? (u=update / r=recreate / g=guac->notes / i=ignore) [u]: ").strip().lower()
            if update_choice in ("", "u", "update"):
                # Multithreaded update execution
                disable_threads = os.environ.get('GUAC_DISABLE_THREADS') == '1'
                from concurrent.futures import ThreadPoolExecutor, as_completed
                from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
                from rich.live import Live
                from rich.table import Table

                def do_update(entry):
                    conn, identifier = entry
                    try:
                        conn_enable_wol = enable_wol and not conn.get('wol_disabled', False)
                        safe_host = selected_hostname or ""
                        guac_api.update_connection(
                            identifier=identifier,
                            name=conn['name'],
                            hostname=safe_host,
                            username=conn['username'],
                            password=conn['password'],
                            port=conn['port'],
                            protocol=conn['protocol'],
                            enable_wol=conn_enable_wol,
                            mac_address=selected_mac or "",
                            parent_identifier=parent_identifier,
                            rdp_settings=conn.get('rdp_settings'),
                            wol_settings=conn.get('wol_settings')
                        )
                        return (conn['name'], None)
                    except Exception as e:
                        return (conn['name'], str(e))

                progress = Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(bar_width=None), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=console)
                task_id = progress.add_task("Updating connections...", total=len(updates_needed))
                status = {c['name']: ("queued", "") for c, _ in updates_needed}

                def build_table():
                    tbl = Table(box=None)
                    tbl.add_column("Name", style="cyan")
                    tbl.add_column("State", style="magenta")
                    tbl.add_column("Result", style="green")
                    for conn, _ in updates_needed:
                        st, res = status.get(conn['name'], ("queued", ""))
                        tbl.add_row(conn['name'], st, res)
                    return tbl

                with Live(build_table(), console=console, refresh_per_second=20):
                    with progress:
                        if disable_threads or len(updates_needed) == 1:
                            progress.update(task_id, description="Updating (sequential mode)...")
                            for entry in updates_needed:
                                conn, _id = entry
                                status[conn['name']] = ("running", "")
                                name, err = do_update(entry)
                                status[name] = ("done", "OK") if not err else ("error", err.split('\n')[0][:60])
                                progress.advance(task_id)
                        else:
                            max_workers = min(8, len(updates_needed))
                            progress.update(task_id, description=f"Updating with {max_workers} workers...")
                            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                future_map = {executor.submit(do_update, entry): entry for entry in updates_needed}
                                for fut in as_completed(future_map):
                                    name, err = fut.result()
                                    status[name] = ("done", "OK") if not err else ("error", err.split('\n')[0][:60])
                                    progress.advance(task_id)
            elif update_choice in ("r", "recreate"):
                for conn, identifier in updates_needed:
                    print(f"Recreating: deleting '{conn['name']}' first")
                    try:
                        guac_api.delete_connection(identifier)
                    except Exception as e:
                        print(f"  Delete failed for {conn['name']}: {e}")
                unique_connections.extend([c for c, _ in updates_needed])
            elif update_choice in ("g", "guac", "guac->notes"):
                # Pull settings from Guacamole into VM notes (bidirectional sync)
                if not is_external_host and vm_node and vm_id:
                    print("\nPulling connection settings from Guacamole to VM notes...")
                    pulled_lines = []
                    for conn, identifier in updates_needed:
                        existing_conn = guac_api.get_connection_details(identifier)
                        if not existing_conn:
                            continue
                        params = existing_conn.get('parameters', {})
                        proto = conn['protocol']
                        port = params.get('port') or str(conn.get('port'))
                        username = params.get('username') or conn.get('username')
                        password = params.get('password') or conn.get('password')
                        # Compose structured line (unencrypted; encryption step will process)
                        line = f'user:"{username}" pass:"{password}" protos:"{proto}" confName:"{conn['name']}";'
                        pulled_lines.append(line)
                    if pulled_lines:
                        try:
                            existing_notes = proxmox_api.get_vm_notes(vm_node, vm_id) or ""
                            # Remove any existing structured lines to avoid duplication
                            note_lines = [l for l in existing_notes.splitlines() if not l.strip().endswith(';')]
                            note_lines.extend(pulled_lines)
                            new_notes = "\n".join(note_lines)
                            if proxmox_api.update_vm_notes(vm_node, vm_id, new_notes):
                                print("  Updated VM notes with Guacamole connection settings")
                                # Trigger encryption/processing pass
                                try:
                                    proxmox_api.process_and_update_vm_notes(vm_node, vm_id, new_notes)
                                except Exception:
                                    pass
                            else:
                                print("  Failed to update VM notes with pulled settings")
                        except Exception as e:
                            print(f"  Error applying pulled settings: {e}")
                else:
                    print("Cannot sync Guacamole settings to notes for external hosts or missing VM context.")
            else:
                print("Ignoring updates (leaving existing connections as-is).")
        else:
            print("Updating existing connections with new details (auto-approve mode)")
            # Auto-approve path: use multithreaded execution too
            disable_threads = os.environ.get('GUAC_DISABLE_THREADS') == '1'
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
            from rich.live import Live
            from rich.table import Table

            def do_update(entry):
                conn, identifier = entry
                try:
                    conn_enable_wol = enable_wol and not conn.get('wol_disabled', False)
                    safe_host = selected_hostname or ""
                    guac_api.update_connection(
                        identifier=identifier,
                        name=conn['name'],
                        hostname=safe_host,
                        username=conn['username'],
                        password=conn['password'],
                        port=conn['port'],
                        protocol=conn['protocol'],
                        enable_wol=conn_enable_wol,
                        mac_address=selected_mac or "",
                        parent_identifier=parent_identifier,
                        rdp_settings=conn.get('rdp_settings'),
                        wol_settings=conn.get('wol_settings')
                    )
                    return (conn['name'], None)
                except Exception as e:
                    return (conn['name'], str(e))

            progress = Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(bar_width=None), TextColumn("{task.completed}/{task.total}"), TimeElapsedColumn(), console=console)
            task_id = progress.add_task("Updating connections...", total=len(updates_needed))
            status = {c['name']: ("queued", "") for c, _ in updates_needed}

            def build_table():
                tbl = Table(box=None)
                tbl.add_column("Name", style="cyan")
                tbl.add_column("State", style="magenta")
                tbl.add_column("Result", style="green")
                for conn, _ in updates_needed:
                    st, res = status.get(conn['name'], ("queued", ""))
                    tbl.add_row(conn['name'], st, res)
                return tbl

            with Live(build_table(), console=console, refresh_per_second=20):
                with progress:
                    if disable_threads or len(updates_needed) == 1:
                        progress.update(task_id, description="Updating (sequential mode)...")
                        for entry in updates_needed:
                            conn, _id = entry
                            status[conn['name']] = ("running", "")
                            name, err = do_update(entry)
                            status[name] = ("done", "OK") if not err else ("error", err.split('\n')[0][:60])
                            progress.advance(task_id)
                    else:
                        max_workers = min(8, len(updates_needed))
                        progress.update(task_id, description=f"Updating with {max_workers} workers...")
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            future_map = {executor.submit(do_update, entry): entry for entry in updates_needed}
                            for fut in as_completed(future_map):
                                name, err = fut.result()
                                status[name] = ("done", "OK") if not err else ("error", err.split('\n')[0][:60])
                                progress.advance(task_id)

    # Handle duplicates (unchanged connections)
    if duplicates:
        print(f"\nFound {len(duplicates)} connection(s) already up-to-date:")
        for name in duplicates:
            print(f"  - {name}")
    
    connections_to_create = unique_connections
    
    if not connections_to_create:
        print("\nWarning: No new connections to create (all already exist)")
        return True

    print(f"\nCreating {len(connections_to_create)} connection(s) (parallel)...")
    created_connections: List[Tuple[str, Optional[str]]] = []
    # Concurrency guard
    disable_threads = os.environ.get('GUAC_DISABLE_THREADS') == '1'
    max_workers = min(8, max(1, len(connections_to_create)))  # cap to keep UI responsive
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from rich.live import Live
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

    def create_one(conn: Dict) -> Tuple[str, Optional[str], Optional[str]]:
        """Worker: create a single connection; returns (name, identifier, error)."""
        try:
            conn_enable_wol = enable_wol and not conn.get('wol_disabled', False)
            proto = conn['protocol']
            if proto == 'rdp':
                safe_host = selected_hostname or ""
                identifier = guac_api.create_rdp_connection(
                    name=conn['name'], hostname=safe_host, username=conn['username'], password=conn['password'], port=conn['port'],
                    enable_wol=conn_enable_wol, mac_address=selected_mac or "", parent_identifier=parent_identifier,
                    rdp_settings=conn.get('rdp_settings'), wol_settings=conn.get('wol_settings')
                )
            elif proto == 'ssh':
                safe_host = selected_hostname or ""
                identifier = guac_api.create_ssh_connection(
                    name=conn['name'], hostname=safe_host, username=conn['username'], password=conn['password'], port=conn['port'],
                    enable_wol=conn_enable_wol, mac_address=selected_mac or "", parent_identifier=parent_identifier,
                    wol_settings=conn.get('wol_settings')
                )
            else:  # vnc
                safe_host = selected_hostname or ""
                identifier = guac_api.create_vnc_connection(
                    name=conn['name'], hostname=safe_host, password=conn['password'], port=conn['port'],
                    enable_wol=conn_enable_wol, mac_address=selected_mac or "", parent_identifier=parent_identifier,
                    wol_settings=conn.get('wol_settings'), vnc_settings=conn.get('vnc_settings')
                )
            
            return conn['name'], identifier, None
        except Exception as e:
            return conn['name'], None, str(e)

    status = {}  # connection name -> (state, msg)
    futures = []
    
    if disable_threads or len(connections_to_create) == 1:
        # Sequential mode for debugging or single connection
        print("Creating connections sequentially...")
        for conn in connections_to_create:
            name, identifier, err = create_one(conn)
            if err:
                status[name] = ("error", err.split('\n')[0][:60])
            else:
                status[name] = ("done", "OK")
                created_connections.append((name, identifier))
    else:
        # Parallel mode with progress display
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Creating connections...", total=len(connections_to_create))
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                for conn in connections_to_create:
                    status[conn['name']] = ("queued", "")
                    futures.append(executor.submit(create_one, conn))
                
                # Collect results as they complete
                for fut in as_completed(futures):
                    name, identifier, err = fut.result()
                    if err:
                        status[name] = ("error", err.split('\n')[0][:60])
                    else:
                        status[name] = ("done", "OK")
                        created_connections.append((name, identifier))
                    progress.advance(task_id)
            # Final refresh
            pass

    successes = [name for name, identifier in created_connections if identifier]
    failures = [name for name, identifier in created_connections if not identifier]

    if successes:
        console.print("\n[green]Successfully created the following connections:[/green]")
        for name in successes:
            console.print(f"  - [cyan]{name}[/cyan]")

        # Mandatory: Update VM notes with encrypted credentials for Proxmox VMs (always attempt)
        if not is_external_host and vm_node and vm_id:
            try:
                console.print("\n[cyan]Processing and updating VM notes to ensure credentials are saved and encrypted...[/cyan]")
                # If vm_notes existed, pass them through the processor; otherwise pass an empty string to prompt creation
                to_process = vm_notes or ""
                updated_notes = proxmox_api.process_and_update_vm_notes(vm_node, vm_id, to_process)
                if updated_notes and updated_notes != vm_notes:
                    console.print("    [green]VM notes updated with credentials/encryption[/green]")
                    vm_notes = updated_notes
                else:
                    # Decide whether we should append structured credentials.
                    # Conditions:
                    #  - We successfully created connections
                    #  - Existing notes are empty OR contain no structured credential lines (legacy format like 'user:pass')
                    try:
                        has_structured = proxmox_api.has_structured_credentials(vm_notes)
                    except Exception:
                        has_structured = False

                    if successes and (not vm_notes or not has_structured):
                        try:
                            lines = []
                            for conn_name, identifier in created_connections:
                                conn = next((c for c in connections_to_create if c['name'] == conn_name), None)
                                if conn:
                                    lines.append(
                                        f'user:"{conn.get("username","")}" pass:"{conn.get("password","")}" protos:"{conn.get("protocol","")}" confName:"{conn.get("name","")}";'
                                    )
                            if lines:
                                new_block = "\n".join(lines)
                                # Append to existing notes (preserve legacy content) or set fresh
                                combined = new_block if not vm_notes else f"{vm_notes.rstrip()}\n\n{new_block}"
                                if proxmox_api.update_vm_notes(vm_node, vm_id, combined):
                                    action = "Appended" if vm_notes else "Saved"
                                    console.print(f"    [green]{action} structured credential lines to VM notes[/green]")
                                    vm_notes = combined
                                else:
                                    console.print("    [yellow]Failed to update VM notes with structured credentials[/yellow]")
                            else:
                                console.print("    [yellow]No credential lines generated to append[/yellow]")
                        except Exception as e:
                            console.print(f"    [yellow]Error while appending structured VM notes: {e}[/yellow]")
                    else:
                        console.print("    [green]VM notes processed (no change needed)[/green]")
            except Exception as e:
                console.print(f"    [yellow]Warning: Could not process/update VM notes: {e}[/yellow]")
                
    if failures:
        console.print(Panel("Failed to create the following connections:", border_style="red"))
        for name in failures:
            console.print(f"  - [red]{name}[/red]")

    if parent_identifier and successes:
        console.print(Panel(f"Connections were grouped under: [cyan]{parent_identifier}[/cyan]", border_style="cyan"))

    if enable_wol and selected_mac:
        if auto_approve:
            console.print("[yellow]Skipping Wake-on-LAN test (auto-approve mode)[/yellow]")
        else:
            test_wol = input("Test Wake-on-LAN now? (y/n) [n]: ").strip().lower()
            if test_wol in ("y", "yes"):
                WakeOnLan.send_wol_packet(selected_mac)
    
    # Offer to restore previous power state if we started the VM (Proxmox VMs only)
    if vm_was_started and original_status in ('stopped', 'shutdown') and not is_external_host:
        if auto_approve:
            console.print(f"[blue]Restoring VM to previous power state ({original_status})[/blue]")
            if vm_node and vm_id and proxmox_api.stop_vm(vm_node, vm_id):
                console.print(f"[green]VM restored to {original_status} state[/green]")
            else:
                console.print(f"[yellow]Failed to restore VM to {original_status} state[/yellow]")
        else:
            restore_choice = input(f"\nRestore VM to previous power state ({original_status})? (y/n) [n]: ").strip().lower()
            if restore_choice in ("y", "yes"):
                if vm_node and vm_id and proxmox_api.stop_vm(vm_node, vm_id):
                    console.print(f"[green]VM restored to {original_status} state[/green]")
                else:
                    console.print(f"[yellow]Failed to restore VM to {original_status} state[/yellow]")

    return len(failures) == 0

def send_wol_manual():
    """Manual Wake-on-LAN function"""
    print("\n" + "="*50)
    print("Send Wake-on-LAN Packet")
    print("="*50)
    
    while True:
        mac_address = input("MAC Address (e.g., 52:54:00:12:34:56): ").strip()
        if WakeOnLan.validate_mac_address(mac_address):
            break
        print("Invalid MAC address format")
    
    broadcast_ip = input("Broadcast IP [255.255.255.255]: ").strip()
    if not broadcast_ip:
        broadcast_ip = "255.255.255.255"
    
    port = input("Port [9]: ").strip()
    port = int(port) if port else 9
    
    return WakeOnLan.send_wol_packet(mac_address, broadcast_ip, port)

def list_connections():
    """List existing Guacamole connections"""
    config = Config()
    guac_api = GuacamoleAPI(config)
    
    if not guac_api.authenticate():
        console.print(Panel(" Failed to authenticate with Guacamole", border_style="red"))
        return False
    
    connections = guac_api.get_connections()
    
    if not connections:
        console.print(Panel(" No connections found.", border_style="yellow"))
        return True
    
    # Pre-build a mapping of connection names to PVE sources for efficiency
    connection_to_pve_source = {}
    try:
        proxmox_api = ProxmoxAPI(config)
        all_vms = proxmox_api.get_vms()
        
        # Group VMs by node for efficient lookup
        vms_by_node = {}
        for vm in all_vms:
            node_name = vm.get('node')
            if node_name not in vms_by_node:
                vms_by_node[node_name] = []
            vms_by_node[node_name].append(vm)
        
        # Build connection name to PVE node mapping
        for node_name, vms in vms_by_node.items():
            for vm in vms:
                vm_id = vm.get('vmid')
                vm_name = vm.get('name', '')
                
                try:
                    vm_config = proxmox_api.get_vm_config(node_name, vm_id)
                    notes = vm_config.get('description', '')
                    
                    if notes:
                        parsed_creds = proxmox_api.parse_credentials_from_notes(
                            notes, vm_name, str(vm_id), node_name
                        )
                        
                        for cred in parsed_creds:
                            connection_name = cred.get('connection_name')
                            if connection_name:
                                connection_to_pve_source[connection_name] = node_name
                except Exception:
                    continue
    except Exception:
        # If Proxmox is not accessible, all connections will show as "Unknown"
        pass
    
    # Create enhanced title with symbols and better formatting
    title_text = f"● Guacamole Connections ({len(connections)} found)"
    table = Table(title=title_text, title_style="bold cyan", show_header=True, header_style="bold magenta")
    
    table.add_column("Connection Name", style="cyan", no_wrap=False, min_width=20, max_width=30)
    table.add_column("Host", style="green", min_width=15, max_width=25)
    table.add_column("Protocol", style="magenta", justify="center", max_width=8)
    table.add_column("Port", style="yellow", justify="center", max_width=6)
    table.add_column("PVE Source", style="orange1", justify="center", max_width=12)
    table.add_column("Sync Status", style="white", justify="center", min_width=12)
    
    for conn_id, conn in connections.items():
        name = conn.get('name', 'N/A')
        protocol = conn.get('protocol', 'N/A')
        
        # Get detailed connection parameters
        conn_details = guac_api.get_connection_details(conn_id)
        params = conn_details.get('parameters', {})
        
        # Enhanced hostname/IP display
        import socket
        ip_address = params.get('hostname', 'N/A')
        display_hostname = ip_address
        
        if ip_address and ip_address != 'N/A':
            try:
                # Try to resolve hostname from IP address
                resolved_hostname = socket.gethostbyaddr(ip_address)[0]
                # Show just the hostname for cleaner display
                if len(resolved_hostname) > 20:
                    # Truncate long hostnames
                    display_hostname = f"{resolved_hostname[:17]}..."
                else:
                    display_hostname = resolved_hostname
            except (socket.herror, socket.gaierror, OSError):
                # If resolution fails, just show the IP address
                display_hostname = ip_address
        
        # Get port from parameters
        port_mapping = {
            'rdp': params.get('port', '3389'),
            'vnc': params.get('port', '5900'),
            'ssh': params.get('port', '22')
        }
        port = port_mapping.get(protocol.lower(), params.get('port', 'N/A'))
        
        # Improved WoL detection and sync status
        wol_send_param = params.get('wol-send-packet', False)
        wol_mac_param = params.get('wol-mac-addr', '')
        
        # Check wol-send-packet parameter
        if isinstance(wol_send_param, str):
            send_packet_enabled = wol_send_param.lower() in ['true', '1', 'yes', 'on']
        elif isinstance(wol_send_param, bool):
            send_packet_enabled = wol_send_param
        else:
            send_packet_enabled = False
        
        # WoL is enabled if both send-packet is true and MAC address is present
        wol_enabled = send_packet_enabled and wol_mac_param and wol_mac_param.strip()
        
        # Get PVE source from pre-built mapping
        pve_source = connection_to_pve_source.get(name, "Manual")
        
        # Enhanced sync status with symbols
        if pve_source != "Manual":
            # Check if port matches expected defaults
            expected_ports = {'rdp': '3389', 'vnc': '5900', 'ssh': '22'}
            expected_port = expected_ports.get(protocol.lower())
            
            if expected_port and port != expected_port:
                sync_status = "[yellow]⚠ Port diff[/yellow]"
            else:
                sync_status = "[green]✓ OK[/green]"
        else:
            sync_status = "[dim]Manual[/dim]"
        
        table.add_row(
            name, 
            display_hostname, 
            protocol.upper(),
            str(port),
            pve_source,
            sync_status
        )
    
    console.print(table)
    return True

def autogroup_connections():
    """Analyze existing connections and suggest automatic groupings"""
    config = Config()
    guac_api = GuacamoleAPI(config)
    
    if not guac_api.authenticate():
        console.print(Panel(" Failed to authenticate with Guacamole", border_style="red"))
        return False
    
    console.print(Panel.fit(" Auto-Group Analysis", border_style="cyan", title="Analyzing Connections"))
    
    # Get connections and groups
    connections = guac_api.get_connections()
    existing_groups = guac_api.get_connection_groups()
    
    if not connections:
        console.print(Panel(" No connections found to analyze.", border_style="yellow"))
        return True
    
    # Get connection details for analysis
    connection_details = {}
    with AnimationManager("Loading connection details", style="cyan") as anim:
        for conn_id, conn in connections.items():
            anim.update(f"Loading {conn.get('name', 'connection')}...")
            details = guac_api.get_connection_details(conn_id)
            connection_details[conn_id] = {
                'name': conn.get('name', ''),
                'protocol': conn.get('protocol', ''),
                'params': details.get('parameters', {}),
                'group': conn.get('parentIdentifier')
            }
    
    # Analyze connections for grouping opportunities
    suggested_groups = analyze_connections_for_grouping(connection_details)
    
    if not suggested_groups:
        console.print(Panel(" No grouping opportunities found.\n All connections are already optimally organized.", border_style="green"))
        return True
    
    # Display suggestions
    console.print(f"\n[bold green]Found {len(suggested_groups)} grouping opportunities:[/bold green]\n")
    
    for i, group in enumerate(suggested_groups, 1):
        console.print(f"[bold cyan]{i}. Suggested Group: '{group['name']}'[/bold cyan]")
        console.print(f"   [yellow]Reason: {group['reason']}[/yellow]")
        console.print(f"   [dim]Connections ({len(group['connections'])}):[/dim]")
        
        for conn in group['connections']:
            protocol = conn['protocol'].upper()
            hostname = conn['params'].get('hostname', 'N/A')
            console.print(f"     • {conn['name']} ({protocol}) - {hostname}")
        console.print()
    
    # Ask user if they want to apply suggestions
    if not typer.confirm("\nApply these grouping suggestions?"):
        console.print("[yellow]Grouping cancelled.[/yellow]")
        return True
    
    # Apply groupings
    console.print("\n[green]Creating connection groups...[/green]")
    
    success_count = 0
    error_count = 0
    
    for group in suggested_groups:
        try:
            # Create the group
            group_identifier = guac_api.create_connection_group(group['name'])
            
            if group_identifier:
                console.print(f"[green]✓ Created group: {group['name']}[/green]")
                
                # Move connections to the group
                moved_count = 0
                for conn in group['connections']:
                    if move_connection_to_group(guac_api, conn['id'], group_identifier):
                        moved_count += 1
                    else:
                        console.print(f"[yellow]  ⚠ Could not move {conn['name']} to group[/yellow]")
                
                console.print(f"[dim]  Moved {moved_count}/{len(group['connections'])} connections[/dim]")
                success_count += 1
            else:
                console.print(f"[red]✗ Failed to create group: {group['name']}[/red]")
                error_count += 1
                
        except Exception as e:
            console.print(f"[red]✗ Error creating group {group['name']}: {e}[/red]")
            error_count += 1
    
    console.print(f"\n[green]Successfully created: {success_count} groups[/green]")
    if error_count > 0:
        console.print(f"[red]Failed to create: {error_count} groups[/red]")
    
    console.print("\n[cyan]Grouping complete! Use 'list' command to see the new organization.[/cyan]")
    return True

def analyze_connections_for_grouping(connection_details):
    """Analyze connections and suggest groupings based on smart patterns"""
    suggestions = []
    ungrouped_connections = []
    
    # Find connections not already in groups
    for conn_id, details in connection_details.items():
        if not details['group'] or details['group'] == 'ROOT':
            ungrouped_connections.append({
                'id': conn_id,
                'name': details['name'],
                'protocol': details['protocol'],
                'params': details['params']
            })
    
    if len(ungrouped_connections) < 2:
        return []
    
    # Group by hostname/IP
    hostname_groups = {}
    for conn in ungrouped_connections:
        hostname = conn['params'].get('hostname', '')
        if hostname:
            if hostname not in hostname_groups:
                hostname_groups[hostname] = []
            hostname_groups[hostname].append(conn)
    
    # Suggest groups for same hostname with multiple connections
    for hostname, connections in hostname_groups.items():
        if len(connections) > 1:
            # Try to extract a meaningful name from connection names
            group_name = suggest_group_name_from_connections(connections, hostname)
            suggestions.append({
                'name': group_name,
                'connections': connections,
                'reason': f'Same hostname/IP: {hostname}'
            })
    
    # Group by similar names (remove protocol suffixes)
    remaining_connections = []
    used_connection_ids = set()
    
    for suggestion in suggestions:
        for conn in suggestion['connections']:
            used_connection_ids.add(conn['id'])
    
    for conn in ungrouped_connections:
        if conn['id'] not in used_connection_ids:
            remaining_connections.append(conn)
    
    # Analyze name patterns for remaining connections
    name_pattern_groups = find_name_pattern_groups(remaining_connections)
    suggestions.extend(name_pattern_groups)
    
    return suggestions

def suggest_group_name_from_connections(connections, hostname):
    """Suggest a meaningful group name from connection names and hostname"""
    names = [conn['name'] for conn in connections]
    
    # Try to find common prefix
    if len(names) > 1:
        common_prefix = os.path.commonprefix(names).strip('-_')
        if len(common_prefix) >= 3:
            return common_prefix
    
    # Try to extract hostname or meaningful part
    try:
        import socket
        resolved_name = socket.gethostbyaddr(hostname)[0]
        if resolved_name and '.' in resolved_name:
            return resolved_name.split('.')[0]
    except:
        pass
    
    # Use the hostname or a cleaned version of the first connection name
    if hostname and not hostname.startswith('192.168.') and not hostname.startswith('10.'):
        return hostname
    
    # Fall back to cleaned first connection name
    first_name = names[0]
    # Remove common suffixes
    for suffix in ['-rdp', '-ssh', '-vnc', '_rdp', '_ssh', '_vnc']:
        if first_name.lower().endswith(suffix):
            return first_name[:-len(suffix)]
    
    return first_name

def find_name_pattern_groups(connections):
    """Find connections that should be grouped based on name patterns"""
    suggestions = []
    
    # Group by base name (removing protocol suffixes)
    base_name_groups = {}
    
    for conn in connections:
        base_name = extract_base_name(conn['name'])
        if base_name not in base_name_groups:
            base_name_groups[base_name] = []
        base_name_groups[base_name].append(conn)
    
    # Suggest groups for base names with multiple connections
    for base_name, conns in base_name_groups.items():
        if len(conns) > 1:
            suggestions.append({
                'name': base_name,
                'connections': conns,
                'reason': f'Similar naming pattern (base: {base_name})'
            })
    
    return suggestions

def extract_base_name(connection_name):
    """Extract base name by removing common protocol and user suffixes"""
    name = connection_name.lower()
    
    # Remove common patterns
    patterns_to_remove = [
        r'-rdp$', r'_rdp$', r'\.rdp$',
        r'-ssh$', r'_ssh$', r'\.ssh$', 
        r'-vnc$', r'_vnc$', r'\.vnc$',
        r'-\d+$',  # Remove port numbers
        r':\d+$'   # Remove :port
    ]
    
    import re
    for pattern in patterns_to_remove:
        name = re.sub(pattern, '', name)
    
    # Remove user@ prefix
    if '@' in name:
        name = name.split('@')[1]
    
    return name.strip('-_.')

def move_connection_to_group(guac_api, connection_id, group_identifier):
    """Move a connection to a specific group"""
    return guac_api.move_connection_to_group(connection_id, group_identifier)

def delete_connections_interactive():
    """Interactive deletion mode for connections and groups"""
    config = Config()
    guac_api = GuacamoleAPI(config)
    
    if not guac_api.authenticate():
        console.print(Panel(" Failed to authenticate with Guacamole", border_style="red"))
        return False
    
    # Get connections and groups
    connections = guac_api.get_connections()
    groups = guac_api.get_connection_groups()
    
    if not connections and not groups:
        console.print(Panel(" No connections or groups found to delete.", border_style="yellow"))
        return True
    
    # Prepare items for selection
    items = []
    
    # Add connections
    for conn_id, conn in connections.items():
        name = conn.get('name', 'N/A')
        protocol = conn.get('protocol', 'N/A')
        items.append({
            'type': 'connection',
            'id': conn_id,
            'name': name,
            'display': f"[Connection] {name} ({protocol.upper()})",
            'selected': False
        })
    
    # Add connection groups
    for group_id, group in groups.items():
        name = group.get('name', 'N/A')
        items.append({
            'type': 'group',
            'id': group_id,
            'name': name,
            'display': f"[Group] {name}",
            'selected': False
        })
    
    if not items:
        console.print(Panel(" No items available for deletion.", border_style="yellow"))
        return True
    
    console.print(Panel.fit(" Delete Connections & Groups", border_style="red", title="Delete Mode"))
    console.print("\n[yellow]Use SPACE to select/deselect items, ENTER to confirm deletion, ESC or Ctrl+C to cancel[/yellow]\n")
    
    current_index = 0
    
    try:
        while True:
            # Clear screen and show selection
            console.clear()
            console.print(Panel.fit(" Delete Connections & Groups", border_style="red", title="Delete Mode"))
            console.print("\n[yellow]Use SPACE to select/deselect, ENTER to delete selected, ESC/Ctrl+C to cancel[/yellow]\n")
            
            # Show items with selection state
            for i, item in enumerate(items):
                prefix = ">" if i == current_index else " "
                checkbox = "[x]" if item['selected'] else "[ ]"
                style = "bold red" if item['selected'] else "white"
                highlight = "on blue" if i == current_index else ""
                
                console.print(f"{prefix} {checkbox} [{style} {highlight}]{item['display']}[/{style} {highlight}]")
            
            selected_count = sum(1 for item in items if item['selected'])
            if selected_count > 0:
                console.print(f"\n[red]{selected_count} item(s) selected for deletion[/red]")
            
            # Get user input
            import sys
            import tty
            import termios
            
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            
            try:
                tty.setraw(sys.stdin.fileno())
                ch = sys.stdin.read(1)
                
                if ch == '\x1b':  # ESC sequence
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        ch3 = sys.stdin.read(1)
                        if ch3 == 'A':  # Up arrow
                            current_index = max(0, current_index - 1)
                        elif ch3 == 'B':  # Down arrow
                            current_index = min(len(items) - 1, current_index + 1)
                    else:
                        # ESC pressed, cancel
                        console.print("\n[yellow]Delete cancelled.[/yellow]")
                        return True
                elif ch == ' ':  # Space - toggle selection
                    items[current_index]['selected'] = not items[current_index]['selected']
                elif ch == '\r' or ch == '\n':  # Enter - confirm deletion
                    selected_items = [item for item in items if item['selected']]
                    if selected_items:
                        break
                    else:
                        console.print("\n[yellow]No items selected for deletion.[/yellow]")
                        input("Press Enter to continue...")
                elif ch == '\x03':  # Ctrl+C
                    console.print("\n[yellow]Delete cancelled.[/yellow]")
                    return True
                        
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                
    except KeyboardInterrupt:
        console.print("\n[yellow]Delete cancelled.[/yellow]")
        return True
    
    # Confirm deletion
    selected_items = [item for item in items if item['selected']]
    if not selected_items:
        console.print("\n[yellow]No items selected for deletion.[/yellow]")
        return True
    
    console.clear()
    console.print("\n[red bold]⚠ CONFIRM DELETION ⚠[/red bold]")
    console.print("\nThe following items will be permanently deleted:")
    
    for item in selected_items:
        console.print(f"  • {item['display']}")
    
    confirm = input(f"\nType 'DELETE' to confirm deletion of {len(selected_items)} item(s): ").strip()
    
    if confirm != "DELETE":
        console.print("\n[yellow]Deletion cancelled - confirmation text did not match.[/yellow]")
        return True
    
    # Perform deletions
    console.print("\n[red]Deleting selected items...[/red]")
    
    success_count = 0
    error_count = 0
    
    for item in selected_items:
        try:
            if item['type'] == 'connection':
                if guac_api.delete_connection(item['id']):
                    console.print(f"[green]✓ Deleted connection: {item['name']}[/green]")
                    success_count += 1
                else:
                    console.print(f"[red]✗ Failed to delete connection: {item['name']}[/red]")
                    error_count += 1
            elif item['type'] == 'group':
                if guac_api.delete_connection_group(item['id']):
                    console.print(f"[green]✓ Deleted group: {item['name']}[/green]")
                    success_count += 1
                else:
                    console.print(f"[red]✗ Failed to delete group: {item['name']}[/red]")
                    error_count += 1
        except Exception as e:
            console.print(f"[red]✗ Error deleting {item['name']}: {e}[/red]")
            error_count += 1
    
    console.print(f"\n[green]Successfully deleted: {success_count}[/green]")
    if error_count > 0:
        console.print(f"[red]Failed deletions: {error_count}[/red]")
    
    input("\nPress Enter to continue...")
    return True

def process_single_vm_auto(config, proxmox_api, guac_api, node_name, vm, credentials, force=False):
    """Process a single VM with automatic configuration"""
    vm_id = vm['vmid']
    vm_name = vm.get('name', f"VM-{vm_id}")
    
    try:
        # Check VM status and start if needed
        vm_status = proxmox_api.get_vm_status(node_name, vm_id)
        original_status = vm_status.get('status', 'unknown')
        vm_was_started = False
        
        if original_status in ('stopped', 'shutdown'):
            console.print(f"   [blue] VM is {original_status}. Starting VM for network detection...[/blue]")
            if proxmox_api.start_vm(node_name, vm_id):
                vm_was_started = True
                console.print("   [yellow] Waiting 30 seconds for VM to boot...[/yellow]")
                import time
                time.sleep(30)
            else:
                console.print(f"   [red]  Failed to start VM {vm_id}[/red]")
        
        # Get network info to find IP
        network_details = proxmox_api.get_vm_network_info(node_name, vm_id)
        
        # Try to find VM IP and collect MACs for WoL
        vm_ip = None
        vm_macs = []
        
        for interface in network_details:
            # Collect MAC addresses for WoL
            mac = (interface.get('mac') or 
                   interface.get('virtio') or 
                   interface.get('e1000') or 
                   interface.get('rtl8139'))
            if mac:
                vm_macs.append(mac)
            
            # Find IP address - IPv4 ONLY (no IPv6)
            for addr in interface.get('ip_addresses', []):
                ip_addr = addr.get('ip-address') or addr.get('address')
                if ip_addr and not ip_addr.startswith('127.') and not ip_addr.startswith('::1'):
                    # Reject IPv6 addresses completely - only accept IPv4
                    if '::' in ip_addr or (':' in ip_addr and '.' not in ip_addr):
                        continue  # Skip IPv6 addresses
                    vm_ip = ip_addr
                    break
            
            if vm_ip:
                break
        
        if not vm_ip:
            # Try network scanning with MAC addresses
            for mac in vm_macs:
                scan_result = NetworkScanner.find_mac_on_network(mac)
                if scan_result:
                    vm_ip = scan_result['ip']
                    console.print(f"   [green] Found VM at IP {vm_ip} via network scan[/green]")
                    break
        
        if not vm_ip:
            console.print(f"   [red] Cannot determine IP address for VM {vm_name}[/red]")
            # Restore VM state before returning
            if vm_was_started and original_status in ('stopped', 'shutdown'):
                console.print(f"   [blue] Restoring VM to {original_status} state...[/blue]")
                proxmox_api.stop_vm(node_name, vm_id)
            return False
        
        # Create connection group for the VM only if there are multiple connections
        parent_identifier = None
        if len(credentials) > 1:
            group_name = vm_name
            console.print(f"   [cyan] Creating connection group: {group_name}[/cyan]")
            parent_identifier = guac_api.create_connection_group(group_name)
            if parent_identifier is None:
                console.print("   [yellow]  Failed to create connection group. Connections will be created at root level.[/yellow]")
        
        # Use the first available MAC for WoL
        primary_mac = vm_macs[0] if vm_macs else None
        
        # Create connections for each credential set (duplicates already handled by caller)
        created_count = 0
        for cred in credentials:
            connection_name = cred['connection_name']
            protocol = cred['protocol'] 
            username = cred['username']
            password = cred['password']
            port = cred.get('port', 3389 if protocol == 'rdp' else (22 if protocol == 'ssh' else 5900))
                        
            # Get WoL and RDP settings from credentials
            wol_disabled = cred.get('wol_disabled', False)
            rdp_settings = cred.get('rdp_settings', {})
            wol_settings = cred.get('wol_settings', {})
            
            # Create connection based on protocol (with parent group)
            identifier = None
            if protocol == 'rdp':
                identifier = guac_api.create_rdp_connection(
                    name=connection_name,
                    hostname=vm_ip,
                    username=username,
                    password=password,
                    port=port,
                    parent_identifier=parent_identifier,
                    enable_wol=(not wol_disabled and primary_mac is not None),
                    mac_address=primary_mac or "",
                    rdp_settings=rdp_settings if rdp_settings else None,
                    wol_settings=wol_settings if wol_settings else None
                )
            elif protocol == 'vnc':
                # Get VNC-specific settings from credentials
                vnc_settings = cred.get('vnc_settings', {})
                identifier = guac_api.create_vnc_connection(
                    name=connection_name,
                    hostname=vm_ip,
                    password=password,
                    port=port,
                    parent_identifier=parent_identifier,
                    enable_wol=(not wol_disabled and primary_mac is not None),
                    mac_address=primary_mac or "",
                    wol_settings=wol_settings if wol_settings else None,
                    vnc_settings=vnc_settings if vnc_settings else None
                )
            elif protocol == 'ssh':
                identifier = guac_api.create_ssh_connection(
                    name=connection_name,
                    hostname=vm_ip,
                    username=username,
                    password=password,
                    port=port,
                    parent_identifier=parent_identifier,
                    enable_wol=(not wol_disabled and primary_mac is not None),
                    mac_address=primary_mac or "",
                    wol_settings=wol_settings if wol_settings else None
                )
            
            if identifier:
                created_count += 1
                console.print(f"   [green] Created {protocol.upper()} connection:[/green] [cyan]{connection_name}[/cyan]")
            else:
                console.print(f"   [red] Failed to create {protocol.upper()} connection:[/red] [yellow]{connection_name}[/yellow]")
        
        # Restore VM state if we started it
        if vm_was_started and original_status in ('stopped', 'shutdown'):
            console.print(f"   [blue] Restoring VM to original state ([cyan]{original_status}[/cyan])...[/blue]")
            if proxmox_api.stop_vm(node_name, vm_id):
                console.print(f"   [green] VM restored to {original_status} state[/green]")
            else:
                console.print(f"   [yellow]  Failed to restore VM to {original_status} state[/yellow]")
        
        return created_count > 0
        
    except Exception as e:
        return False


def auto_process_all_vms(force=False):
    """Auto-process all VMs with credentials in notes with enhanced output."""
    import time
    import threading
    from rich.text import Text
    
    # Enhanced header with better styling
    title_text = Text("● AUTO VM PROCESSOR", style="bold cyan")
    console.print(Panel(
        title_text,
        title="[bold]Auto Processor[/bold]",
        border_style="blue",
        padding=(0, 2)
    ))
    
    if force:
        console.print(Panel(
            "[bold yellow]FORCE MODE:[/bold yellow] Recreating all existing connections",
            border_style="yellow"
        ))
    
    # Initialize services with Rich progress
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Initializing services...", total=None)
        
        try:
            config = Config()
            proxmox_api = ProxmoxAPI(config)
            guac_api = GuacamoleAPI(config)
            
            # Test connections
            nodes = proxmox_api.get_nodes()
            guac_api.authenticate()
            guac_api.get_connections()
            
            progress.update(task, description="Services initialized successfully!")
            progress.stop()
            console.print("[green]✓[/green] Services initialized successfully!")
            
        except Exception as e:
            progress.stop()
            console.print(f"[red]✗ Failed to initialize services: {e}[/red]")
            return
    
    # Find VMs with credentials using Rich progress
    vms_with_creds = []
    
    console.print("\n[bold]● Scanning for VMs with credentials[/bold]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        scanning_task = progress.add_task("Scanning nodes...", total=len(nodes))
        
        for i, node in enumerate(nodes):
            node_name = node['node']
            progress.update(scanning_task, description=f"Scanning node: {node_name}")
            
            # Get VMs for this node
            vms = proxmox_api.get_vms(node_name)
            
            for vm in vms:
                vm_id = vm['vmid']
                
                # Get VM config to check notes
                try:
                    vm_config = proxmox_api.get_vm_config(node_name, vm_id)
                    notes = vm_config.get('description', '')
                    
                    # Parse credentials from notes
                    parsed_creds = proxmox_api.parse_credentials_from_notes(notes, vm.get('name', ''), str(vm_id), node_name, 'unknown')
                    if parsed_creds:
                        vms_with_creds.append({
                            'node': node_name,
                            'vm': vm,
                            'credentials': parsed_creds
                        })
                except:
                    continue
            
            progress.advance(scanning_task)
        
        progress.update(scanning_task, description=f"Found {len(vms_with_creds)} VMs with credentials!")
    
    console.print(f"[green]✓[/green] Found [bold]{len(vms_with_creds)}[/bold] VMs with credentials!")
    
    if not vms_with_creds:
        console.print(Panel(
            "[yellow]No VMs found with credentials in notes[/yellow]\n\n"
            "Add credentials to VM notes in the format:\n"
            '[cyan]user:"admin" pass:"password" protos:"rdp,ssh"[/cyan]',
            title="[yellow]No Credentials Found[/yellow]",
            border_style="yellow"
        ))
        return
    
    # Process each VM with enhanced Rich progress
    console.print(f"\n[bold]● Processing [cyan]{len(vms_with_creds)}[/cyan] VMs[/bold]")
    
    success_count = 0
    skip_count = 0
    error_count = 0
    
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "•",
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        main_task = progress.add_task("Processing VMs...", total=len(vms_with_creds))
        
        for i, vm_data in enumerate(vms_with_creds):
            vm = vm_data['vm']
            node_name = vm_data['node']
            creds = vm_data['credentials']
            
            vm_name = vm.get('name', f"VM-{vm['vmid']}")
            progress.update(main_task, description=f"Processing: {vm_name}")
            
            console.print(f"\n[bold cyan]● {vm_name}[/bold cyan] [dim]({i+1}/{len(vms_with_creds)})[/dim]")
            
            # Check if ALL connections for this VM already exist (proper duplicate checking)
            all_exist = True
            existing_connections = []
            
            for cred in creds:
                connection_name = cred['connection_name']
                existing = guac_api.get_connection_by_name(connection_name)
                if existing:
                    existing_connections.append((connection_name, existing))
                else:
                    all_exist = False
            
            if all_exist and not force:
                console.print("  [yellow]⏭ All connections already exist (use --force to recreate)[/yellow]")
                skip_count += len(creds)
                progress.advance(main_task)
                continue
            
            if existing_connections and force:
                console.print(f"  [yellow]● Removing {len(existing_connections)} existing connection(s)[/yellow]")
                for conn_name, existing in existing_connections:
                    try:
                        success = guac_api.delete_connection(existing['identifier'])
                        if success:
                            console.print(f"    [green]✓[/green] Deleted: {conn_name}")
                        else:
                            console.print(f"    [red]✗[/red] Could not delete: {conn_name}")
                    except Exception as e:
                        console.print(f"    [red]✗[/red] Failed to delete {conn_name}: {e}")
            
            # Process VM
            try:
                console.print("  [cyan]● Processing connections...[/cyan]")
                
                # Actually process the VM - simplified auto processing
                result = process_single_vm_auto(config, proxmox_api, guac_api, node_name, vm, creds, force)
                
                if result:
                    console.print("  [green]✓ Successfully added![/green]")
                    success_count += 1
                else:
                    console.print("  [red]✗ Failed to add[/red]")
                    error_count += 1
                    
            except Exception as e:
                console.print(f"  [red]✗ Error: {str(e)[:50]}...[/red]")
                error_count += 1
            
            progress.advance(main_task)
    
    # Enhanced summary
    console.print("\n" + "="*60)
    console.print(Panel.fit(
        "[bold green]● PROCESSING COMPLETE![/bold green]",
        border_style="green",
        padding=(0, 2)
    ))
    
    # Create summary table
    summary_table = Table(show_header=False, padding=(0, 2))
    summary_table.add_column("Metric", style="cyan", min_width=20)
    summary_table.add_column("Count", style="white", justify="right")
    summary_table.add_column("Status", style="white")
    
    summary_table.add_row("Successfully processed", str(success_count), "[green]✓[/green]" if success_count > 0 else "")
    summary_table.add_row("Skipped (existing)", str(skip_count), "[yellow]⏭[/yellow]" if skip_count > 0 else "")  
    summary_table.add_row("Errors", str(error_count), "[red]✗[/red]" if error_count > 0 else "")
    summary_table.add_row("Total VMs processed", str(len(vms_with_creds)), "")
    
    console.print(summary_table)
    
    if success_count > 0:
        console.print(f"\n[bold green]{success_count} new connections ready in Guacamole![/bold green]")
    
    console.print("=" * 60)


@app.command("add")
def add_vm():
    """ Add new VM connection to Guacamole"""
    try:
        interactive_add_vm()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        raise typer.Exit()
    except Exception as e:
        console.print(f"[red]Error adding VM: {e}[/red]")
        raise typer.Exit(1)

@app.command("add-external")
def add_external_host():
    """Add a non-Proxmox external host connection to Guacamole"""
    try:
        interactive_add_vm(start_external=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        raise typer.Exit()
    except Exception as e:
        console.print(f"[red]Error adding external host: {e}[/red]")
        raise typer.Exit(1)

@app.command("list")
def list_connections_cmd():
    """ List existing Guacamole connections"""
    try:
        list_connections()
    except Exception as e:
        console.print(f"[red]Error listing connections: {e}[/red]")
        raise typer.Exit(1)

@app.command("test-auth")
def test_auth():
    """ Test API Authentication (Both Proxmox and Guacamole)"""
    from rich.text import Text
    
    # Create header panel
    console.print(Panel.fit(
        Text(" API Authentication Test", style="bold cyan"),
        border_style="cyan",
        padding=(0, 2)
    ))
    
    try:
        config = Config()
        all_passed = True
        
        # Step 1: Encryption Key Validation
        console.print("\n[bold]● Testing API Authentication[/bold]")
        
        step_symbol = "✓"
        try:
            from cryptography.fernet import Fernet
            key = getattr(config, 'ENCRYPTION_KEY', None)
            if key:
                f = Fernet(key)
                test_plain = b"verification-test"
                token = f.encrypt(test_plain)
                if f.decrypt(token) == test_plain:
                    console.print(f"[green]{step_symbol}[/green] Validating encryption key")
                else:
                    console.print("[red]✗[/red] Encryption key round-trip failed")
                    all_passed = False
            else:
                console.print("[yellow]⚠[/yellow] No encryption key configured")
        except Exception as e:
            console.print(f"[red]✗[/red] Encryption key validation failed: {e}")
            all_passed = False
        
        # Step 2: Guacamole Authentication
        try:
            guac_api = GuacamoleAPI(config)
            if guac_api.authenticate(silent=True):
                console.print(f"[green]{step_symbol}[/green] Testing Guacamole authentication")
            else:
                console.print("[red]✗[/red] Guacamole authentication failed")
                all_passed = False
        except Exception as e:
            console.print(f"[red]✗[/red] Guacamole authentication error: {e}")
            all_passed = False
        
        # Step 3: Proxmox Authentication
        try:
            proxmox_api = ProxmoxAPI(config)
            # Override the test_auth to not print its own panel
            response = proxmox_api.session.get(f"{config.proxmox_base_url}/version")
            if response.status_code == 200:
                console.print(f"[green]{step_symbol}[/green] Testing Proxmox authentication")
            else:
                console.print(f"[red]✗[/red] Proxmox authentication failed: HTTP {response.status_code}")
                all_passed = False
        except Exception as e:
            console.print(f"[red]✗[/red] Proxmox authentication error: {e}")
            all_passed = False
        
        # Final result
        if all_passed:
            console.print(f"\n[green]{step_symbol}[/green] All authentication tests passed")
            console.print("\n[dim]Ready to sync VM connections![/dim]")
        else:
            console.print("\n[red]✗[/red] Some authentication tests failed")
            console.print("\n[dim]Please check your configuration and try again.[/dim]")
            raise typer.Exit(1)
            
    except Exception as e:
        console.print(f"\n[red]✗ Error during authentication testing: {e}[/red]")
        raise typer.Exit(1)

@app.command("debug-vms")
def debug_vms():
    """ Debug VM listing with full API response"""
    try:
        config = Config()
        proxmox_api = ProxmoxAPI(config)
        nodes = proxmox_api.get_nodes()
        
        for node in nodes:
            node_name = node['node']
            
            console.print(Panel(f"Node: [cyan]{node_name}[/cyan]", border_style="blue"))
            
            # Check QEMU VMs
            qemu_url = f"{config.proxmox_base_url}/nodes/{node_name}/qemu"
            qemu_response = proxmox_api.session.get(qemu_url)
            
            table = Table(title="QEMU VMs Debug Info")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")
            table.add_row("URL", qemu_url)
            table.add_row("Status Code", str(qemu_response.status_code))
            table.add_row("Response", qemu_response.text[:200] + "..." if len(qemu_response.text) > 200 else qemu_response.text)
            console.print(table)
            
            # Check LXC containers
            lxc_url = f"{config.proxmox_base_url}/nodes/{node_name}/lxc"
            lxc_response = proxmox_api.session.get(lxc_url)
            
            table = Table(title="LXC Containers Debug Info")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")
            table.add_row("URL", lxc_url)
            table.add_row("Status Code", str(lxc_response.status_code))
            table.add_row("Response", lxc_response.text[:200] + "..." if len(lxc_response.text) > 200 else lxc_response.text)
            console.print(table)
            
    except Exception as e:
        console.print(f"[red]Error debugging VMs: {e}[/red]")
        raise typer.Exit(1)

@app.command("test-network")
def test_network(
    mac: str = typer.Argument(..., help="MAC address to scan for on the network")
):
    """ Test network scanning for specific MAC address"""
    try:
        console.print(f"[cyan]Testing network scan for MAC:[/cyan] [yellow]{mac}[/yellow]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Scanning network...", total=None)
            result = NetworkScanner.find_mac_on_network(mac)
            progress.update(task, completed=True)
        
        if result:
            table = Table(title=" Network Scan Result")
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="green")
            table.add_row("IP Address", result['ip'])
            table.add_row("Hostname", result.get('hostname', 'N/A'))
            console.print(table)
        else:
            console.print(Panel(" MAC address not found on network", border_style="red"))
    except Exception as e:
        console.print(f"[red]Error testing network: {e}[/red]")
        raise typer.Exit(1)

@app.command("auto")
def auto_process(
    force: bool = typer.Option(False, "--force", "-f", help="Force mode: recreate all connections and overwrite duplicates")
):
    """ Auto-process all VMs with credentials in notes"""
    try:
        auto_process_all_vms(force=force)
    except KeyboardInterrupt:
        console.print("\n[yellow]Auto-processing cancelled by user.[/yellow]")
        raise typer.Exit()
    except Exception as e:
        console.print(f"[red]Error in auto-processing: {e}[/red]")
        raise typer.Exit(1)

@app.command("delete")
def delete_connections_cmd():
    """ Interactive connection and group deletion mode"""
    try:
        delete_connections_interactive()
    except Exception as e:
        console.print(f"[red]Error in delete mode: {e}[/red]")
        raise typer.Exit(1)

@app.command("autogroup")
def autogroup_connections_cmd():
    """ Automatically group connections using smart pattern analysis"""
    try:
        autogroup_connections()
    except Exception as e:
        console.print(f"[red]Error in autogroup mode: {e}[/red]")
        raise typer.Exit(1)

@app.command("interactive")
def interactive_menu():
    """ Interactive menu mode"""
    from rich.text import Text
    from rich.columns import Columns
    from rich.align import Align
    
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("GUAC_SKIP_INTERACTIVE") or os.environ.get("CI"):
        return
        
    # Enhanced welcome header
    header_text = Text("Guacamole VM Manager", style="bold cyan", justify="center")
    console.print(Panel(
        header_text,
        border_style="cyan",
        title="[bold]Welcome[/bold]",
        padding=(1, 2)
    ))
    
    try:
        while True:
            # Enhanced menu with better visual structure
            menu_items = [
                ("1", "● Manually add Proxmox VM", "cyan"),
                ("2", "● Auto-process all configured VMs", "green"), 
                ("3", "● Add external host connection", "yellow"),
                ("4", "● List existing connections", "blue"),
                ("5", "● Delete connections & groups", "red"),
                ("6", "● Auto-group connections", "magenta"),
                ("7", "● Show CLI command reference", "orange1"),
                ("0", "● Exit application", "white")
            ]
            
            console.print("\n[bold]Available Actions:[/bold]")
            
            # Create a nicely formatted menu table
            menu_table = Table(show_header=False, show_lines=False, padding=(0, 1))
            menu_table.add_column("Choice", style="bold white", width=3)
            menu_table.add_column("Action", min_width=30)
            
            for num, desc, color in menu_items:
                menu_table.add_row(f"[{color}]{num}[/{color}]", f"[{color}]{desc}[/{color}]")
            
            console.print(menu_table)
            
            # Enhanced prompt using Rich console.input for proper markup rendering
            choice = console.input("\n[bold cyan]Enter your choice[/bold cyan] [dim](0-7)[/dim]: ").strip()

            if choice == "1":
                interactive_add_vm()
            elif choice == "2":
                auto_process_all_vms(force=False)
            elif choice == "3":
                interactive_add_vm(start_external=True)
            elif choice == "4":
                list_connections()
            elif choice == "5":
                delete_connections_interactive()
            elif choice == "6":
                autogroup_connections()
            elif choice == "7":
                # Enhanced CLI reference display
                console.print(Panel.fit(
                    "[bold]CLI Command Reference[/bold]",
                    border_style="magenta",
                    padding=(0, 2)
                ))
                
                cli_table = Table(show_header=True, header_style="bold magenta")
                cli_table.add_column("Command", style="cyan", min_width=15)
                cli_table.add_column("Description", style="white")
                
                commands = [
                    ("interactive", "Interactive menu (current mode)"),
                    ("add", "Manually add one Proxmox VM"),
                    ("auto", "Auto-process all VMs with credentials"),
                    ("auto --force", "Force recreate all connections"),
                    ("list", "List existing connections"),
                    ("delete", "Interactive deletion mode"),
                    ("autogroup", "Smart connection grouping"),
                    ("test-auth", "Test API authentication"),
                    ("test-network", "Test network scanning for MAC"),
                    ("add-external", "Add non-Proxmox host"),
                    ("--onboarding", "Rerun setup wizard")
                ]
                
                for cmd, desc in commands:
                    cli_table.add_row(cmd, desc)
                
                console.print(cli_table)
                
            elif choice == "0":
                console.print(Panel(
                    "[bold green]Thank you for using Guacamole VM Manager![/bold green]",
                    border_style="green",
                    padding=(0, 2)
                ))
                break
            else:
                console.print(Panel(
                    f"[red]Invalid choice: '{choice}'[/red]\nPlease enter a number between 0-7",
                    border_style="red",
                    title="[red]Error[/red]"
                ))
    
    except KeyboardInterrupt:
        console.print(Panel(
            "[yellow]Operation cancelled by user[/yellow]",
            border_style="yellow"
        ))
    except Exception as e:
        console.print(Panel(
            f"[red]Unexpected error: {e}[/red]",
            border_style="red",
            title="[red]Error[/red]"
        ))
        raise typer.Exit(1)

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, onboarding: bool = typer.Option(False, "--onboarding", help="Run first-time onboarding wizard")):
    """ Guacamole VM Manager - Sync Proxmox VMs with Apache Guacamole"""
    if ctx.invoked_subcommand is None:
        import sys
        if (
            os.environ.get("PYTEST_CURRENT_TEST")
            or os.environ.get("GUAC_SKIP_INTERACTIVE")
            or os.environ.get("CI")
            or not sys.stdin.isatty()
        ):
            return
        # Onboarding auto-run if sentinel absent or flag provided
        if onboarding or not os.path.exists(ONBOARD_SENTINEL):
            run_onboarding()
        interactive_menu()

if __name__ == "__main__":
    app()