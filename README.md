# Proxmox-Guacamole Sync

Automated synchronization tool that bridges Proxmox VE and Apache Guacamole by parsing VM connection credentials from Proxmox VM notes and creating corresponding remote desktop/SSH connections in Guacamole.

## Architecture

- **Single-file Python application** (`guac_vm_manager.py`) - 2800+ lines, no framework dependencies
- **REST API integration** - Direct HTTP calls to both Proxmox and Guacamole APIs
- **Credential parsing engine** - Flexible syntax parser for VM notes field
- **Network discovery** - ARP/ping scanning when Proxmox guest agent unavailable
- **Built-in Wake-on-LAN** - No external WoL dependencies

## Core Features

### VM Credential Management
- Parse connection credentials from Proxmox VM notes using flexible key-value syntax
- Support for RDP, VNC, and SSH protocols with protocol-specific settings
- Automatic password encryption/decryption using Fernet symmetric encryption
- Template variable substitution: `{vmname}`, `{user}`, `{proto}`, `{port}`, `{vmid}`, `{node}`, `{ip}`, `{hostname}`

### Network Intelligence
- **IPv4-only networking** - Filters IPv6 addresses for clean connections
- Guest agent IP detection with ARP table + ping sweep fallback
- Automatic VM startup for IP discovery with state restoration
- Local network scanning assumes tool runs on same subnet as VMs

### Synchronization Logic  
- Bidirectional sync detection (Proxmox ↔ Guacamole)
- Out-of-sync remediation: update existing, recreate, or pull from Guacamole
- Duplicate connection detection and cleanup
- Connection grouping per VM when multiple users/protocols exist

## VM Notes Syntax

Credentials are stored in Proxmox VM notes using structured key-value format:

```
user:"admin" pass:"P@ssw0rd" protos:"rdp,vnc" rdp_port:"3390" confName:"{vmname}-{user}-{proto}";
user:"readonly" pass:"view123" protos:"vnc" vnc_settings:"read-only=true,color-depth=16";
```

### Supported Parameters
- `user`/`username` - Connection username
- `pass`/`password`/`encrypted_password` - Plain or encrypted password  
- `protos`/`protocols`/`proto` - Comma-separated protocol list
- `confName`/`connection_name` - Template for connection naming
- `{proto}_port` - Protocol-specific port (rdp_port, vnc_port, ssh_port)
- `{proto}_settings` - Protocol configuration (comma-separated key=value pairs)
- `wol_disabled` - Disable Wake-on-LAN for this connection

### VNC-Specific Settings
```
vnc_settings:"color-depth=32,encoding=tight,read-only=false,cursor=local"
```
- `color-depth`: 8, 16, 24, 32 (bit depth)
- `encoding`: raw, rre, corre, hextile, zlib, tight, ultra
- `cursor`: local, remote
- `read-only`: true/false (view-only mode)

## Installation

### Requirements
- Python 3.8+
- Network access to both Proxmox and Guacamole servers
- Proxmox API token with VM read permissions  
- Guacamole admin account with connection management rights

### Setup
```bash
# Clone repository
git clone https://github.com/SpotlightForBugs/Proxmox-Guacamole-Sync.git
cd Proxmox-Guacamole-Sync

# Install dependencies (UV recommended)
uv pip install -r requirements.txt

# Configure credentials
cp config_example.py config.py
# Edit config.py with your API endpoints and credentials

# Test configuration
uv run python guac_vm_manager.py test-auth
```

### Configuration
Edit `config.py` with your environment details:

```python
class Config:
    # Guacamole API
    GUAC_BASE_URL = "https://guacamole.example.com"
    GUAC_USERNAME = "admin"
    GUAC_PASSWORD = "admin_password"
    GUAC_DATA_SOURCE = "mysql"  # or postgresql
    
    # Proxmox API  
    PROXMOX_HOST = "192.168.1.100"
    PROXMOX_TOKEN_ID = "root@pam!token_name"
    PROXMOX_SECRET = "token_secret"
    
    # Password encryption (optional)
    ENCRYPTION_KEY = "your_fernet_key_here"
```

## Usage

### Command Line Interface
```bash
# Interactive VM selection and connection setup
uv run python guac_vm_manager.py add

# Auto-process all VMs with credential notes
uv run python guac_vm_manager.py auto

# Force recreate all connections (ignoring existing)
uv run python guac_vm_manager.py auto --force

# List existing connections with sync status
uv run python guac_vm_manager.py list

# Interactive connection deletion
uv run python guac_vm_manager.py delete

# Add external (non-Proxmox) host connections
uv run python guac_vm_manager.py add-external

# Test API authentication
uv run python guac_vm_manager.py test-auth

# Test network discovery for specific MAC
uv run python guac_vm_manager.py test-network "aa:bb:cc:dd:ee:ff"

# Full interactive menu (default if no command)
uv run python guac_vm_manager.py
```

### Workflow Examples

**Initial Setup:**
1. Add credentials to VM notes in Proxmox web interface
2. Run `uv run python guac_vm_manager.py add` to select VMs interactively
3. Tool discovers VM IPs, creates Guacamole connections, and sets up Wake-on-LAN

**Bulk Processing:**
```bash
uv run python guac_vm_manager.py auto
# Processes all VMs with structured credential notes
# Skips VMs without credentials or with existing up-to-date connections
```

**Maintenance:**
```bash
uv run python guac_vm_manager.py list
# Shows sync status: OK, password mismatch, port change, etc.
# Run with --verbose for detailed connection parameters
```

## API Integration

### Proxmox API
- **Authentication**: Token-based (preferred) or username/password
- **Endpoints**: `/nodes`, `/vms`, `/status` for VM discovery and management
- **VM Notes**: Primary data source for connection credentials
- **Guest Agent**: Optional for IP detection, falls back to network scanning

### Guacamole API  
- **Authentication**: Username/password with session token
- **Endpoint Discovery**: Tries multiple paths (`/guacamole/api`, `/api`) and data sources
- **Connection Management**: Full CRUD operations on connections and groups
- **Parameter Mapping**: Direct mapping from VM credentials to Guacamole connection parameters

## Network Discovery

When Proxmox guest agent is unavailable or reports no IP:

1. **ARP Table Scan**: Parse local system ARP table for VM MAC addresses
2. **Ping Sweep**: Scan subnet ranges (192.168.x.0/24, 10.x.x.0/24) for responsive hosts  
3. **MAC Matching**: Correlate ping responses with VM MAC addresses from Proxmox
4. **IP Assignment**: Use discovered IP for connection creation

## Security

### Password Protection
- **Encryption**: Optional Fernet symmetric encryption for passwords in VM notes
- **Migration**: Automatic conversion from plain `pass:` to `encrypted_password:` format
- **Key Management**: Encryption key stored in `config.py` (git-ignored)

### Network Security
- **SSL Verification Disabled**: Supports self-signed certificates on both APIs
- **Local Network Assumption**: Tool designed to run within same network as managed VMs
- **API Token Storage**: Proxmox tokens in config file, not in code

## Error Handling

- **API Failures**: Progressive fallback through multiple endpoints
- **Network Issues**: Graceful degradation when IPs unavailable  
- **Credential Parsing**: Continues processing on malformed credential lines
- **State Recovery**: VM power state restoration after discovery operations

## Development

### Architecture Notes
- **No ORM/Framework**: Direct REST API calls with manual JSON handling
- **Single-file Design**: All logic contained in `guac_vm_manager.py`
- **Rich Terminal UI**: Progress bars, tables, and interactive prompts
- **Modern CLI**: Typer framework with comprehensive help text

### Testing
```bash
# Run test suite
uv run pytest

# Test specific components
uv run python guac_vm_manager.py --debug-vms    # Debug VM discovery
uv run python guac_vm_manager.py test-network "MAC"  # Test network scanning
```

### Key Classes
- `GuacamoleAPI`: Handles authentication, connection CRUD, group management
- `ProxmoxAPI`: VM discovery, credential parsing, network resolution  
- `NetworkScanner`: ARP table parsing, ping sweep, IP correlation
- `WakeOnLan`: UDP broadcast for remote VM power management

## License

MIT License - See LICENSE file for details

## Contributing

1. Fork repository
2. Create feature branch
3. Add tests for new functionality  
4. Ensure no hardcoded credentials in commits
5. Submit pull request

## Troubleshooting

### Common Issues
- **Authentication failures**: Verify API credentials and endpoint URLs
- **No VMs discovered**: Check Proxmox token permissions and network connectivity
- **IP detection fails**: Ensure tool runs on same network as VMs, check guest agent status
- **Connection creation fails**: Verify Guacamole admin permissions and data source configuration

### Debug Commands
```bash
# Test both API connections
uv run python guac_vm_manager.py test-auth

# Show detailed VM information
uv run python guac_vm_manager.py --debug-vms

# Test network discovery
uv run python guac_vm_manager.py test-network "vm:mac:address"
```