# Proxmox-Guacamole Sync - AI Coding Agent Instructions

## Project Architecture

This is a single-file Python tool (`guac_vm_manager.py`) that bridges Proxmox VE and Apache Guacamole by parsing VM credentials from Proxmox VM notes and automatically creating remote connections (RDP/VNC/SSH) in Guacamole.

### Repository UI/Text Policy
- Emojis are NOT allowed in code or documentation output. Use neutral Unicode symbols when needed (examples: `●`, `○`, `*`, `✔`, `⚠`). This repository enforces a strict no-emoji rule for terminal interfaces, logs, and docs.

### Core Components

- **GuacamoleAPI**: Handles authentication, connection CRUD, and group management via REST API
- **ProxmoxAPI**: Manages VM discovery, credential parsing from notes, and network scanning
- **NetworkScanner**: Local network scanning to find VM IPs via ARP/ping when Proxmox doesn't have guest agent info
- **WakeOnLan**: Built-in WoL implementation (no external dependencies)

### Key Data Flow

1. **VM Discovery**: List VMs from all Proxmox nodes → Parse credentials from VM notes field
2. **Network Resolution**: Find actual VM IP via network scanning (ARP table + ping sweep)
3. **Connection Creation**: Create/update Guacamole connections with parsed credentials + WoL settings
4. **Grouping**: Organize multiple users per VM into connection groups

## Critical Patterns

### VM Notes Credential Format
The core feature parses flexible credential syntax from Proxmox VM notes:
```
user:"admin" pass:"password" protos:"rdp,ssh" rdp_port:"3390" confName:"{vmname}-{user}-{proto}";
```

**Key parsing logic in `parse_credentials_from_notes()`:**
- Parameters can be in ANY order
- Multiple protocols per user line (comma-separated)
- Template variables: `{vmname}`, `{user}`, `{proto}`, `{port}`, `{vmid}`, `{node}`, `{ip}`, `{hostname}`
- Password encryption support via cryptography.fernet

### Config Pattern
Uses a simple class-based config (`config.py` copied from `config_example.py`):
```python
class Config:
    GUAC_BASE_URL = "https://guacamole-server.com"
    PROXMOX_HOST = "192.168.1.100"
    PROXMOX_TOKEN_ID = "root@pam!tokenname"
    # ...
```

### API Resilience
Both APIs implement endpoint fallback patterns:
- **Guacamole**: Tries multiple data sources (mysql/postgresql) and paths (/guacamole/api vs /api) with clean authentication UI
- **Proxmox**: Token-based auth with proper error handling for missing guest agents

### PVE Source Tracking
- **VM Notes Integration**: Tracks PVE source by parsing VM notes to find which connections originated from which Proxmox node
- **Visual Identification**: Connection listing shows PVE source to distinguish VM-created connections from manual ones
- **Efficient Caching**: Pre-builds connection-to-node mapping for fast display without repeated API calls
- **Future-proofing**: Supports mixed environments with both Proxmox and manually added connections

## Development Workflows

### Setup & Testing
```bash
# Setup (uses UV package manager)
cp config_example.py config.py  # Edit with real credentials
uv pip install -r requirements.txt

# Core testing commands
uv run python guac_vm_manager.py --test-auth      # Test both API connections
uv run python guac_vm_manager.py --debug-vms     # Debug VM discovery
uv run python guac_vm_manager.py --test-network "aa:bb:cc:dd:ee:ff"  # Test network scanning
```

### Main Usage Patterns
```bash
# Modern Typer CLI Commands
uv run python guac_vm_manager.py add             # Interactive VM selection
uv run python guac_vm_manager.py auto            # Process all VMs with credentials
uv run python guac_vm_manager.py auto --force    # Force recreate all connections
uv run python guac_vm_manager.py list            # List existing connections
uv run python guac_vm_manager.py test-auth       # Test API authentication
uv run python guac_vm_manager.py interactive     # Full interactive menu
# Default (no command) runs interactive mode
```

### VM State Management
- **Auto-Start**: Automatically starts stopped VMs during setup for IP detection
- **State Restoration**: Restores VMs to original power state after connection creation
- **30-second Boot Wait**: Built-in delay for network interface initialization

## Project-Specific Conventions

### Modern CLI Architecture
- **Single-file architecture**: All logic in `guac_vm_manager.py` (2800+ lines)
- **Typer CLI**: Modern command-line interface with subcommands and rich help
- **Rich Output**: Colorful tables, panels, and progress indicators with clean authentication status
- **Minimal dependencies**: requests, urllib3, cryptography, typer, rich
- **No ORM/framework**: Direct REST API calls with manual JSON handling
- **Silent Authentication**: Clean endpoint probing with professional status display

### Error Handling Style
Uses **progressive fallback** rather than strict validation:
- Multiple API endpoint attempts before failing
- Network scanning with ARP + ping combination
- Graceful degradation when guest agents unavailable

### Security Patterns
- **Password encryption**: Optional Fernet encryption for passwords stored in VM notes
- **SSL warnings disabled**: Self-signed certificate support for both APIs
- **Credential isolation**: config.py git-ignored, sensitive data in VM notes only

## Integration Points

### Proxmox Integration
- **VM Notes Field**: Primary credential storage (user-editable in Proxmox web UI)
- **Guest Agent**: Optional for IP detection, falls back to network scanning
- **API Tokens**: Preferred over username/password auth

### Guacamole Integration
- **Connection Groups**: Auto-created per VM (named after VM) to organize all users
- **Protocol Support**: RDP, VNC, SSH with protocol-specific settings
- **Update Logic**: Smart detection of existing connections to avoid duplicates
- **Grouping Logic**: All connections for a VM are organized under VM-named groups

### Network Dependencies
- **Local Network Scanning**: Assumes tool runs on same network as VMs
- **Wake-on-LAN**: Direct UDP broadcast to wake powered-off VMs before connecting

## Testing Structure
- `tests/unit/`: Mock-based unit tests for API classes
- `tests/integration/`: End-to-end workflow tests
- `pytest.ini`: Empty (using defaults)

When modifying this codebase, maintain the single-file architecture and focus on the credential parsing logic as the core differentiator.