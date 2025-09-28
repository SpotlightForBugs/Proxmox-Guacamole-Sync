#!/usr/bin/env python3
"""
Guacamole VM Manager

A script to add VMs to Guacamole and manage Wake-on-LAN functionality.
Integrates with Guacamole and Proxmox APIs for seamless VM management.

Author: Johannes
Date: September 27, 2025
"""

import requests
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
    help=" Guacamole VM Manager - Sync Proxmox VMs with Apache Guacamole",
    rich_markup_mode="rich"
)

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
        
    def authenticate(self) -> bool:
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
        
        # Show clean authentication status
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        
        with console.status(" Authenticating with Guacamole...", spinner="dots"):
            for endpoint in endpoints:
                auth_url = urljoin(self.config.GUAC_BASE_URL, endpoint)
                
                try:
                    # Use form-encoded data as per examples found online
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
            connection_data = {
                "name": name,
                "protocol": "vnc",
                "parentIdentifier": parent_identifier or "ROOT",
                "parameters": {
                    "hostname": hostname,
                    "port": str(port),
                    "password": password
                },
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

        for endpoint in self._build_api_endpoints(f"connections/{identifier}"):
            try:
                response = self.session.put(endpoint, json=connection_data)
                if response.status_code in (200, 204):
                    print(f"Updated connection '{name}' (ID: {identifier})")
                    return True
                elif response.status_code == 404:
                    continue
                else:
                    print(f"Failed to update connection via {endpoint}: {response.status_code} {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to update connection via {endpoint}: {e}")
                continue

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
                            parent_identifier: Optional[str] = None, wol_settings: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Create VNC connection in Guacamole"""
        if not self.auth_token:
            if not self.authenticate():
                return None
        
        connection_data = {
            "name": name,
            "protocol": "vnc",
            "parentIdentifier": parent_identifier or "ROOT",
            "parameters": {
                "hostname": hostname,
                "port": str(port),
                "password": password
            },
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
                print("Proxmox authentication successful")
                return True
            else:
                print(f"Proxmox authentication failed: HTTP {response.status_code}")
                if response.text:
                    print(f"Response: {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"Proxmox authentication error: {e}")
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
            response.raise_for_status()
            print(f"Updated VM {vmid} notes with encrypted passwords")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Failed to update VM notes: {e}")
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
        # Example: user:"admin" pass:"pass123" protos:"rdp,ssh" rdp_port:"3389" ssh_port:"22" confName:"template" wolDisabled:"true";
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
                    'wol_settings': wol_overrides,
                    'wol_disabled': wol_disabled
                })
        
        return credentials

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
            response = self.session.post(agent_url, json={})
            if response.status_code != 200:
                print(f" Guest agent query returned status {response.status_code} for VM {vmid}")
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
                # Skip link-local and loopback
                if not ip_address:
                    continue
                if ip_address.startswith('127.') or ip_address.startswith('::1'):
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

def interactive_add_vm(auto_approve: bool = False):
    """Interactive function to add VM to Guacamole"""
    config = Config()
    guac_api = GuacamoleAPI(config)
    proxmox_api = ProxmoxAPI(config)
    
    print("\n" + "="*50)
    print("Add VM to Guacamole")
    print("="*50)
    
    # Authenticate with Guacamole
    if not guac_api.authenticate():
        print("Failed to authenticate with Guacamole")
        return False
    
    # Get VMs from Proxmox
    print("\nFetching VMs from Proxmox...")
    if not proxmox_api.test_auth():
        return False
    vms = proxmox_api.get_vms()
    
    if vms:
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
                
                    if notes:
                        parsed_creds = proxmox_api.parse_credentials_from_notes(
                            notes, vm_name, str(vm_id), node_name
                        )
                    
                        if parsed_creds:
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
        
            print(f"\nFound {len(vms)} VMs in Proxmox:")
            if vms_with_unconfigured_creds:
                print(f"* {len(vms_with_unconfigured_creds)} VMs ready for setup (have credentials in notes)")
            if vms_with_configured_creds:
                print(f"✔ {len(vms_with_configured_creds)} VMs already configured")
        
            print("-" * 90)
            print(f"{'#':<4} {'ID':<6} {'Name':<20} {'Node':<10} {'Status':<10} {'Memory':<8} {'Ready':<10}")
            print("-" * 90)
        
            for idx, vm in enumerate(prioritized_vms, start=1):
                status = vm.get('status', 'N/A')
                status_icon = "●" if status == "running" else "○" if status == "stopped" else ""
            
                # Determine if VM is ready for setup
                ready_status = ""
                if vm in vms_with_unconfigured_creds:
                    ready_status = "* Ready"
                elif vm in vms_with_configured_creds:
                    ready_status = "✔ Done"
            
                print(f"{idx:<4} {vm.get('vmid', 'N/A'):<6} {vm.get('name', 'N/A'):<20} {vm.get('node', 'N/A'):<10} {status_icon} {status:<8} {vm.get('mem', 'N/A'):<8} {ready_status}")
        
            # Update vms to use the prioritized order
            vms = prioritized_vms
    else:
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
    
    print("\n" + "-" * 50)
    print("Options:")
    print("  • Select VM by ID, name, or number from list above")
    print("  • Type 'external' for non-Proxmox host")
    
    selected_vm = None
    is_external_host = False
    vm_lookup_by_id = {str(vm.get('vmid')): vm for vm in vms}
    vm_lookup_by_name = {vm.get('name', '').lower(): vm for vm in vms if vm.get('name')}

    while not selected_vm:
        selection = input("Selection: ").strip()
        if not selection:
            print("A selection is required")
            continue

        # Check for external host option
        if selection.lower() in ('external', 'ext', 'e'):
            is_external_host = True
            # Create a fake VM entry for external host
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

        print(f"\nSelected VM: {vm_name} (ID: {vm_id}, Node: {vm_node})")

        # Check VM status
        vm_status = proxmox_api.get_vm_status(vm_node, vm_id)
        original_status = vm_status.get('status', 'unknown')
        print(f"VM Status: {original_status}")
        
        # Get VM notes for credential parsing
        vm_notes = proxmox_api.get_vm_notes(vm_node, vm_id)
        parsed_credentials = proxmox_api.parse_credentials_from_notes(vm_notes, vm_name, str(vm_id), vm_node, "unknown")
        
        if parsed_credentials:
            print(f"\n Found {len(parsed_credentials)} credential set(s) in VM notes:")
            for i, cred in enumerate(parsed_credentials, 1):
                print(f"  {i}. {cred['username']} ({cred['protocol']}) - {cred['connection_name']}")
        else:
            print("\nWarning: No credentials found in VM notes")

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
                # Skip loopback and link-local addresses
                if ip_addr.startswith('127.') or ip_addr.startswith('169.254.') or ip_addr.startswith('::1') or ip_addr.startswith('fe80:'):
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
            print(f" Found {len(guest_agent_ips)} IP(s) from guest agent (highest priority)")
        else:
            print("  No IPs found from guest agent")
            print("    To enable: install qemu-guest-agent in VM and restart")

        selected_hostname = None

        # Add scanned IP to options if found (lower priority than guest agent)
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
    selected_hostname = None
    
    if ip_options:
        print("\nDiscovered IP addresses:")
        for idx, option in enumerate(ip_options, start=1):
            source_icon = "●" if option.get('source') == 'guest_agent' else "○" if option.get('source') == 'network_scan' else ""
            print(f"  {idx}. {source_icon} {option['label']}")
        if not auto_approve:
            print("  m.  Enter manually")

        chosen: Optional[Dict] = None
        if auto_approve:
            chosen = ip_options[0]
            print(f"Auto-selected: {chosen['label']}")
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
            
        # Update parsed credentials with actual IP if we have it (Proxmox VMs only)
        if selected_hostname and selected_hostname != "unknown" and parsed_credentials and not is_external_host:
            if vm_id is not None and vm_node:
                parsed_credentials = proxmox_api.parse_credentials_from_notes(vm_notes, vm_name, str(vm_id), vm_node, selected_hostname)
    else:
        # No IP options found - provide helpful guidance
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

    if not selected_hostname:
        print("Unable to determine hostname for the connection.")
        return False
    selected_hostname = str(selected_hostname)

    # Prefer ARP-scanned MAC if available, otherwise use first available
    if found_mac:
        selected_mac = found_mac
        print(f"\nUsing network-discovered MAC: {found_mac}")
    elif not selected_mac and mac_candidates:
        selected_mac = mac_candidates[0]['mac']

    if mac_candidates and not auto_approve:
        print("\nAvailable MAC addresses:")
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
            print(f"  {idx}. {label}")
        print("  m. Enter manually")

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
    if not auto_approve:
        default_protocol = input("Default protocol for connections (rdp/vnc/ssh): ").strip().lower()
        if default_protocol not in ("rdp", "vnc", "ssh"):
            print("Warning: Invalid protocol. Protocols must be specified in VM notes.")
            return False

        if default_protocol == "rdp":
            default_port = config.DEFAULT_RDP_PORT
        elif default_protocol == "ssh":
            default_port = 22
        else:  # vnc
            default_port = config.DEFAULT_VNC_PORT
            
        port_input = input(f"Default port for {default_protocol.upper()} connections [{default_port}]: ").strip()
        if port_input:
            try:
                default_port = int(port_input)
            except ValueError:
                print("Warning: Invalid port specified. Using default.")
    else:
        print("Auto-approve mode: Protocols and settings must be specified in VM notes")

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
        
        if not auto_approve:
            confirm = input("\nUse these credentials from VM notes? (y/n) [y]: ").strip().lower()
            if confirm != "" and confirm not in ("y", "yes"):
                parsed_credentials = []  # Fall back to manual entry
                connections_to_create = []
    
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

            protocol = input("Protocol for this connection (rdp/vnc/ssh): ").strip().lower()
            if protocol not in ("rdp", "vnc", "ssh"):
                print("Error: Please specify a valid protocol (rdp/vnc/ssh)")
                continue

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
                    print("Warning: Invalid port. Using default for this connection.")

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
    # Always create a connection group for VM connections (not just multiple connections from same session)
    if not is_external_host:
        if auto_approve:
            group_name = vm_name
            print(f"Creating connection group: {group_name}")
            parent_identifier = guac_api.create_connection_group(group_name)
            if parent_identifier is None:
                print("Warning: Failed to create connection group. Connections will be created at root level.")
        else:
            group_choice = input("Create a connection group for VM connections? (y/n) [y]: ").strip().lower()
            if group_choice == "" or group_choice in ("y", "yes"):
                default_group_name = vm_name
                group_name = input(f"Group name [{default_group_name}]: ").strip()
                if not group_name:
                    group_name = default_group_name
                parent_identifier = guac_api.create_connection_group(group_name)
                if parent_identifier is None:
                    print("Warning: Failed to create connection group. Connections will be created at root level.")
    elif len(connections_to_create) > 1:
        # For external hosts, only create group if multiple connections from same session
        if auto_approve:
            group_name = vm_name
            print(f"Creating connection group: {group_name}")
            parent_identifier = guac_api.create_connection_group(group_name)
            if parent_identifier is None:
                print("Warning: Failed to create connection group. Connections will be created at root level.")
        else:
            group_choice = input("Create a connection group for these entries? (y/n) [y]: ").strip().lower()
            if group_choice == "" or group_choice in ("y", "yes"):
                default_group_name = vm_name
                group_name = input(f"Group name [{default_group_name}]: ").strip()
                if not group_name:
                    group_name = default_group_name
                parent_identifier = guac_api.create_connection_group(group_name)
                if parent_identifier is None:
                    print("Warning: Failed to create connection group. Connections will be created at root level.")

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
            update_choice = input("\nUpdate existing connections with new details? (y/n) [y]: ").strip().lower()
            if update_choice == "" or update_choice in ("y", "yes"):
                for conn, identifier in updates_needed:
                    # Check if WoL should be disabled for this specific connection
                    conn_enable_wol = enable_wol and not conn.get('wol_disabled', False)
                    guac_api.update_connection(
                        identifier=identifier,
                        name=conn['name'],
                        hostname=selected_hostname,
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
        else:
            print("Updating existing connections with new details (auto-approve mode)")
            for conn, identifier in updates_needed:
                # Check if WoL should be disabled for this specific connection
                conn_enable_wol = enable_wol and not conn.get('wol_disabled', False)
                guac_api.update_connection(
                    identifier=identifier,
                    name=conn['name'],
                    hostname=selected_hostname,
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

    # Handle duplicates (unchanged connections)
    if duplicates:
        print(f"\nFound {len(duplicates)} connection(s) already up-to-date:")
        for name in duplicates:
            print(f"  - {name}")
    
    connections_to_create = unique_connections
    
    if not connections_to_create:
        print("\nWarning: No new connections to create (all already exist)")
        return True

    print(f"\nCreating {len(connections_to_create)} connection(s)...")
    created_connections: List[Tuple[str, Optional[str]]] = []

    for conn in connections_to_create:
        # Check if WoL should be disabled for this specific connection
        conn_enable_wol = enable_wol and not conn.get('wol_disabled', False)
        
        if conn['protocol'] == 'rdp':
            identifier = guac_api.create_rdp_connection(
                name=conn['name'],
                hostname=selected_hostname,
                username=conn['username'],
                password=conn['password'],
                port=conn['port'],
                enable_wol=conn_enable_wol,
                mac_address=selected_mac or "",
                parent_identifier=parent_identifier,
                rdp_settings=conn.get('rdp_settings'),
                wol_settings=conn.get('wol_settings')
            )
        elif conn['protocol'] == 'ssh':
            identifier = guac_api.create_ssh_connection(
                name=conn['name'],
                hostname=selected_hostname,
                username=conn['username'],
                password=conn['password'],
                port=conn['port'],
                enable_wol=conn_enable_wol,
                mac_address=selected_mac or "",
                parent_identifier=parent_identifier,
                wol_settings=conn.get('wol_settings')
            )
        else:  # vnc
            identifier = guac_api.create_vnc_connection(
                name=conn['name'],
                hostname=selected_hostname,
                password=conn['password'],
                port=conn['port'],
                enable_wol=conn_enable_wol,
                mac_address=selected_mac or "",
                parent_identifier=parent_identifier,
                wol_settings=conn.get('wol_settings')
            )

        created_connections.append((conn['name'], identifier))

    successes = [name for name, identifier in created_connections if identifier]
    failures = [name for name, identifier in created_connections if not identifier]

    if successes:
        print("\nSuccessfully created the following connections:")
        for name in successes:
            print(f"  - {name}")
            
        # Update VM notes with encrypted credentials for Proxmox VMs
        if not is_external_host and vm_node and vm_id and vm_notes:
            print("\n Updating VM notes with encrypted credentials...")
            try:
                # Process and update the VM notes to encrypt any plain text passwords
                updated_notes = proxmox_api.process_and_update_vm_notes(vm_node, vm_id, vm_notes)
                if updated_notes != vm_notes:
                    print("    VM notes updated with encrypted passwords")
                else:
                    print("   ℹ  VM notes already contain encrypted passwords")
            except Exception as e:
                print(f"     Warning: Could not update VM notes: {e}")
                
    if failures:
        print("\nFailed to create the following connections:")
        for name in failures:
            print(f"  - {name}")

    if parent_identifier and successes:
        print(f"\nConnections were grouped under: {parent_identifier}")

    if enable_wol and selected_mac:
        if auto_approve:
            print("Skipping Wake-on-LAN test (auto-approve mode)")
        else:
            test_wol = input("Test Wake-on-LAN now? (y/n) [n]: ").strip().lower()
            if test_wol in ("y", "yes"):
                WakeOnLan.send_wol_packet(selected_mac)
    
    # Offer to restore previous power state if we started the VM (Proxmox VMs only)
    if vm_was_started and original_status in ('stopped', 'shutdown') and not is_external_host:
        if auto_approve:
            print(f"Restoring VM to previous power state ({original_status})")
            if vm_node and vm_id and proxmox_api.stop_vm(vm_node, vm_id):
                print(f"VM restored to {original_status} state")
            else:
                print(f"Failed to restore VM to {original_status} state")
        else:
            restore_choice = input(f"\nRestore VM to previous power state ({original_status})? (y/n) [n]: ").strip().lower()
            if restore_choice in ("y", "yes"):
                if vm_node and vm_id and proxmox_api.stop_vm(vm_node, vm_id):
                    print(f"VM restored to {original_status} state")
                else:
                    print(f"Failed to restore VM to {original_status} state")

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
    
    table = Table(title=f" Guacamole Connections ({len(connections)} found)")
    table.add_column("Name", style="cyan", no_wrap=True, max_width=18)
    table.add_column("Protocol", style="magenta", max_width=8)
    table.add_column("Hostname/IP", style="green", min_width=15, max_width=25)
    table.add_column("PVE Source", style="yellow", justify="center", max_width=10)
    table.add_column("WoL", justify="center", style="blue", max_width=5)
    
    for conn_id, conn in connections.items():
        name = conn.get('name', 'N/A')
        protocol = conn.get('protocol', 'N/A')
        
        # Get detailed connection parameters
        conn_details = guac_api.get_connection_details(conn_id)
        params = conn_details.get('parameters', {})
        

        
        # Improved hostname resolution: try to resolve hostname by IP, show both when available
        import socket
        ip_address = params.get('hostname', 'N/A')
        display_hostname = ip_address
        
        if ip_address and ip_address != 'N/A':
            try:
                # Try to resolve hostname from IP address
                resolved_hostname = socket.gethostbyaddr(ip_address)[0]
                # Show both hostname and IP for clarity: "hostname (IP)"
                display_hostname = f"{resolved_hostname} ({ip_address})"
            except (socket.herror, socket.gaierror, OSError):
                # If resolution fails, just show the IP address
                display_hostname = ip_address
        
        # Improved WoL detection: check for wol-send-packet and wol-mac-addr
        wol_send_param = params.get('wol-send-packet', False)
        wol_mac_param = params.get('wol-mac-addr', '')
        
        # WoL is enabled if wol-send-packet is true AND there's a MAC address
        wol_enabled = 'No'  # Default to No
        
        # Check wol-send-packet parameter
        if isinstance(wol_send_param, str):
            send_packet_enabled = wol_send_param.lower() in ['true', '1', 'yes', 'on']
        elif isinstance(wol_send_param, bool):
            send_packet_enabled = wol_send_param
        else:
            send_packet_enabled = False
        
        # WoL is enabled if both send-packet is true and MAC address is present
        if send_packet_enabled and wol_mac_param and wol_mac_param.strip():
            wol_enabled = 'Yes'
        
        # Get PVE source from pre-built mapping
        pve_source = connection_to_pve_source.get(name, "Manual")
        
        table.add_row(name, protocol.upper(), display_hostname, pve_source, wol_enabled)
    
    console.print(table)
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
            
            # Find IP address
            for addr in interface.get('ip_addresses', []):
                ip_addr = addr.get('ip-address') or addr.get('address')
                if ip_addr and not ip_addr.startswith('127.') and '::' not in ip_addr:
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
        
        # Create connection group for the VM
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
                identifier = guac_api.create_vnc_connection(
                    name=connection_name,
                    hostname=vm_ip,
                    password=password,
                    port=port,
                    parent_identifier=parent_identifier,
                    enable_wol=(not wol_disabled and primary_mac is not None),
                    mac_address=primary_mac or "",
                    wol_settings=wol_settings if wol_settings else None
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
    """Auto-process all VMs with credentials in notes with beautiful output."""
    import time
    import threading
    
    # Beautiful header
    print("\n" + "=" * 60)
    print(" AUTO VM PROCESSOR")
    print("=" * 60)
    
    if force:
        print(" FORCE MODE: Recreating all existing connections")
    
    # Initialize services
    print("\n Initializing services...")
    loading_chars = ""
    loading_stop = threading.Event()
    
    def loading_animation():
        i = 0
        while not loading_stop.is_set():
            print(f"\r   {loading_chars[i % len(loading_chars)]} Loading...", end="", flush=True)
            i += 1
            time.sleep(0.1)
    
    loading_thread = threading.Thread(target=loading_animation, daemon=True)
    loading_thread.start()
    
    try:
        config = Config()
        proxmox_api = ProxmoxAPI(config)
        guac_api = GuacamoleAPI(config)
        
        # Test connections
        nodes = proxmox_api.get_nodes()
        guac_api.authenticate()
        guac_api.get_connections()
        
        loading_stop.set()
        loading_thread.join()
        print("\r Services initialized successfully!                    ")
        
    except Exception as e:
        loading_stop.set()
        loading_thread.join()
        print(f"\r Failed to initialize services: {e}                    ")
        return
    
    # Find VMs with credentials
    print("\n Scanning for VMs with credentials...")
    vms_with_creds = []
    
    progress_chars = ""
    for i, node in enumerate(nodes):
        node_name = node['node']
        print(f"\r   {progress_chars[i % len(progress_chars)]} Scanning node: {node_name}...", end="", flush=True)
        
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
    
    print(f"\r Found {len(vms_with_creds)} VMs with credentials!                    ")
    
    if not vms_with_creds:
        print("\n No VMs found with credentials in notes.")
        print("   Add credentials to VM notes in the format:")
        print("   username:myuser")
        print("   password:mypass")
        print("   protocols:rdp,vnc")
        return
    
    # Process each VM
    print(f"\n Processing {len(vms_with_creds)} VMs...")
    print("-" * 60)
    
    success_count = 0
    skip_count = 0
    error_count = 0
    
    for i, vm_data in enumerate(vms_with_creds):
        vm = vm_data['vm']
        node_name = vm_data['node']
        creds = vm_data['credentials']
        
        vm_name = vm.get('name', f"VM-{vm['vmid']}")
        progress = f"[{i+1}/{len(vms_with_creds)}]"
        
        print(f"\n{progress}   {vm_name}")
        
        # Fancy progress bar
        bar_width = 30
        filled = int((i / len(vms_with_creds)) * bar_width)
        bar = "" * filled + "" * (bar_width - filled)
        percentage = int((i / len(vms_with_creds)) * 100)
        print(f"   Progress: |{bar}| {percentage}%")
        
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
            print(f"   ⏭  All connections already exist (use --force to recreate)")
            skip_count += len(creds)
            continue
        
        if existing_connections and force:
            print(f"     Removing {len(existing_connections)} existing connection(s)...")
            for conn_name, existing in existing_connections:
                try:
                    success = guac_api.delete_connection(existing['identifier'])
                    if success:
                        print(f"       Deleted: {conn_name}")
                    else:
                        print(f"        Could not delete: {conn_name}")
                except Exception as e:
                    print(f"       Failed to delete {conn_name}: {e}")
        
        # Process VM
        try:
            print(f"    Processing...")
            
            # Animate processing
            process_chars = ""
            for j in range(10):  # Short animation
                print(f"\r   {process_chars[j % len(process_chars)]} Processing...", end="", flush=True)
                time.sleep(0.1)
            
            # Actually process the VM - simplified auto processing
            result = process_single_vm_auto(config, proxmox_api, guac_api, node_name, vm, creds, force)
            
            if result:
                print(f"\r    Successfully added!                    ")
                success_count += 1
            else:
                print(f"\r    Failed to add                        ")
                error_count += 1
                
        except Exception as e:
            print(f"\r    Error: {str(e)[:50]}...                ")
            error_count += 1
    
    # Final progress bar
    bar_width = 30
    bar = "" * bar_width
    print(f"\n   Progress: |{bar}| 100%")
    
    # Summary
    print("\n" + "=" * 60)
    print(" PROCESSING COMPLETE!")
    print("=" * 60)
    print(f" Successfully processed: {success_count}")
    print(f"⏭  Skipped (existing):    {skip_count}")
    print(f" Errors:                {error_count}")
    print(f" Total VMs processed:   {len(vms_with_creds)}")
    
    if success_count > 0:
        print(f"\n {success_count} new connections ready in Guacamole!")
    
    print("\n" + "=" * 60)


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
    """ Test Proxmox API authentication"""
    try:
        config = Config()
        proxmox_api = ProxmoxAPI(config)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Testing authentication...", total=None)
            result = proxmox_api.test_auth()
            progress.update(task, completed=True)
            
        if result:
            console.print(Panel(" Authentication successful!", border_style="green"))
        else:
            console.print(Panel(" Authentication failed!", border_style="red"))
    except Exception as e:
        console.print(f"[red]Error testing authentication: {e}[/red]")
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

@app.command("interactive")
def interactive_menu():
    """ Interactive menu mode"""
    console.print(Panel.fit(" Guacamole VM Manager", border_style="cyan", title="Welcome"))
    
    try:
        while True:
            console.print("\n[bold cyan]Select an option:[/bold cyan]")
            console.print("1.  Add VM to Guacamole")
            console.print("2.  List existing connections")
            console.print("3.  Auto-process all VMs with credentials")
            console.print("4.  Exit")
            
            choice = typer.prompt("\nEnter choice (1-4)").strip()
            
            if choice == "1":
                interactive_add_vm()
            elif choice == "2":
                list_connections()
            elif choice == "3":
                force = typer.confirm("Force mode (recreate existing connections)?", default=False)
                auto_process_all_vms(force=force)
            elif choice == "4":
                console.print(Panel(" Goodbye!", border_style="green"))
                break
            else:
                console.print("[red]Invalid choice. Please enter 1-4.[/red]")
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Exiting...[/yellow]")
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """ Guacamole VM Manager - Sync Proxmox VMs with Apache Guacamole"""
    if ctx.invoked_subcommand is None:
        # If no subcommand is provided, run interactive mode
        interactive_menu()

if __name__ == "__main__":
    app()