# Proxmox-Guacamole Sync

<div align="center">
  <img src="assets/logo.png" alt="Proxmox-Guacamole Sync Logo" width="400">
</div>

[![DeepSource](https://app.deepsource.com/gh/SpotlightForBugs/Proxmox-Guacamole-Sync.svg/?label=active+issues&show_trend=true&token=ve86Yayt5stmo_wDrIby5Vwj)](https://app.deepsource.com/gh/SpotlightForBugs/Proxmox-Guacamole-Sync/)

[![DeepSource](https://app.deepsource.com/gh/SpotlightForBugs/Proxmox-Guacamole-Sync.svg/?label=resolved+issues&show_trend=true&token=ve86Yayt5stmo_wDrIby5Vwj)](https://app.deepsource.com/gh/SpotlightForBugs/Proxmox-Guacamole-Sync/)

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
- **Automatic password encryption** - All passwords encrypted transparently using Fernet
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

<details>
<summary><strong>⚿ Automatic Password Encryption (Built-in Security)</strong></summary>

The tool automatically encrypts all passwords for security. When an encryption key is configured, plain passwords in VM notes are automatically detected and encrypted during processing - no user intervention required.

### How It Works

1. **Automatic Detection**: Tool scans VM notes for plain `pass:"password"` entries
2. **Automatic Encryption**: Plain passwords are immediately encrypted during processing
3. **Seamless Migration**: Converts `pass:"plaintext"` to `encrypted_password:"gAAAAAB..."`
4. **Non-Destructive**: Preserves all other content in VM notes
5. **Transparent Usage**: Encrypted passwords work exactly like plain passwords

### Setup Encryption

**Generate encryption key:**
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: b8kR9X2mF1nQ7vP4sE6tA5wK3hL9mN0pQ2rT8uY7iO1kL4sE6=
```

**Add to config.py:**
```python
class Config:
    # ... other settings ...
    ENCRYPTION_KEY = "b8kR9X2mF1nQ7vP4sE6tA5wK3hL9mN0pQ2rT8uY7iO1kL4sE6="
```

### Example Migration

**Before (plain password):**
```
user:"admin" pass:"MyPassword123" protos:"rdp,ssh";
```

**After automatic migration:**
```
user:"admin" encrypted_password:"gAAAAABhZ8X2mF1nQ7vP4sE6tA5wK3hL9mN0pQ2rT8uY7iO1kL4sE6..." protos:"rdp,ssh";
```

### Benefits

- **Automatic Security**: All passwords encrypted at rest in VM notes without user action
- **Zero Configuration**: Works transparently once encryption key is set
- **Seamless Migration**: Plain passwords automatically converted during first use
- **Backward Compatibility**: Handles both plain and encrypted passwords during transition
- **Key Validation**: Encryption key tested on startup to prevent issues

</details>

## Example Outputs

### Main Help Command
```bash
$ uv run python guac_vm_manager.py --help
```

![Main Help Output](assets/help-output.svg)

### Authentication Test
```bash
$ uv run python guac_vm_manager.py test-auth
```

![Authentication Test Output](assets/test-auth-output.svg)

### List Command Help (New Filtering Options)
```bash
$ uv run python guac_vm_manager.py list --help
```

![List Help Output](assets/list-help-output.svg)

### Add Command Help (Proxmox VM Options)
```bash
$ uv run python guac_vm_manager.py add --help
```

![Add Help Output](assets/add-help-output.svg)

### Edit Command Help (Regex Pattern Matching)
```bash
$ uv run python guac_vm_manager.py edit --help
```

![Edit Help Output](assets/edit-help-output.svg)

### Delete Command Help (Pattern Matching & Bulk Operations)
```bash
$ uv run python guac_vm_manager.py delete --help
```

![Delete Help Output](assets/delete-help-output.svg)

### Add-External Command Help (Partial Options)
```bash
$ uv run python guac_vm_manager.py add-external --help
```

![Add-External Help Output](assets/add-external-help-output.svg)

### VM Discovery and Connection Creation
```bash
$ uv run python guac_vm_manager.py add
```

![VM Discovery Output](assets/vm-discovery-output.svg)

### Connection Listing with Sync Status
```bash
$ uv run python guac_vm_manager.py list
```

![Connection List Output](assets/connection-list-output.svg)

### Auto-Processing All VMs
```bash
$ uv run python guac_vm_manager.py auto
```

![Auto-Processing Output](assets/auto-process-output.svg)

### Network Discovery Example
```bash
$ uv run python guac_vm_manager.py test-network "52:54:00:12:34:56"
```

![Network Discovery Output](assets/network-discovery-output.svg)

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
    
    # Password encryption (required for automatic encryption)
    ENCRYPTION_KEY = "your_fernet_key_here"
```

<details>
<summary><strong>⊞ Docker Deployment for Guacamole (Infrastructure Setup)</strong></summary>

If you need to deploy Guacamole itself, here are recommended Docker Compose configurations optimized for this tool:

### Basic Guacamole (Recommended)
```yaml
version: "3.8"

services:
  guacamole:
    image: abesnier/guacamole
    container_name: guacamole
    network_mode: "host"   # Essential: allows broadcast packets for Wake-on-LAN
    volumes:
      - postgres:/config
    restart: unless-stopped

volumes:
  postgres:
    driver: local
```

### With Cloudflare Tunnel (Advanced)
```yaml
version: "3.8"

services:
  guacamole:
    image: abesnier/guacamole
    container_name: guacamole
    network_mode: "host"   # Essential: allows broadcast packets for Wake-on-LAN
    volumes:
      - postgres:/config
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    network_mode: "host"
    restart: unless-stopped
    command: tunnel --config /etc/cloudflared/config.yml run
    volumes:
      - ./cloudflared:/etc/cloudflared

volumes:
  postgres:
    driver: local
```

### Deployment Notes
- **`network_mode: "host"`** is **critical** for Wake-on-LAN functionality
- Use Proxmox LXC container for optimal performance and network access
- The `abesnier/guacamole` image includes PostgreSQL and is well-maintained
- Cloudflare tunnel provides secure external access without port forwarding

### Proxmox LXC Setup
```bash
# Create LXC container in Proxmox
pct create 200 ubuntu-22.04-standard_22.04-1_amd64.tar.zst \
  --storage local-lvm \
  --cores 2 \
  --memory 2048 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --hostname guacamole-lxc

# Install Docker in LXC
pct start 200
pct exec 200 -- bash -c "curl -fsSL https://get.docker.com | sh"
pct exec 200 -- systemctl enable --now docker

# Deploy Guacamole
pct exec 200 -- mkdir -p /opt/guacamole
# Copy docker-compose.yml to /opt/guacamole/
pct exec 200 -- bash -c "cd /opt/guacamole && docker compose up -d"
```

</details>

## Usage

### Command Line Interface

All interactive menu options are available as direct CLI commands for automation and scripting. Each command supports extensive options for non-interactive usage, including **partial options** (missing required fields prompt for input) and **advanced pattern matching** with regex support:

#### New Features in Latest Version:
- **Partial Options**: Commands like `add --vm-id 100` will prompt for missing required fields instead of failing
- **Regex Pattern Matching**: Use patterns like `.*-admin-.*` or `web-server-.*` for bulk operations
- **Comma-Separated Multiple Patterns**: `edit --connection "server-.*,db-.*"` matches multiple patterns

```bash
# Add Proxmox VM connections (partial options supported - missing fields prompt)
uv run python guac_vm_manager.py add --vm-id 100 --node pve --auto-approve
uv run python guac_vm_manager.py add --vm-id 100 --node pve --hostname 192.168.1.10 --protocol rdp --port 3389 --wol --mac "52:54:00:12:34:56"
uv run python guac_vm_manager.py add --vm-id 100  # Prompts for missing node, hostname, etc.

# Add external host connections (fully non-interactive or partial)
uv run python guac_vm_manager.py add-external --hostname server.example.com --username admin --password-stdin --protocol ssh --port 22 < password.txt
uv run python guac_vm_manager.py add-external --hostname 192.168.1.100 --name "My Server" --username user --password pass --protocol rdp --wol --mac "aa:bb:cc:dd:ee:ff"
uv run python guac_vm_manager.py add-external --hostname server.example.com  # Prompts for username, password, protocol, etc.

# Auto-process VMs with advanced filtering and options
uv run python guac_vm_manager.py auto --force --node pve --start-vms --restore-power
uv run python guac_vm_manager.py auto --vm "web-server" --dry-run --no-skip-existing

# List connections with advanced regex filtering and export options
uv run python guac_vm_manager.py list --vm "windows-*" --protocol rdp --status ok
uv run python guac_vm_manager.py list --connection ".*-admin-.*" --group "Production.*" --json
uv run python guac_vm_manager.py list --csv connections.csv --protocol vnc

# Edit connections with regex pattern matching (bulk operations supported)
uv run python guac_vm_manager.py edit --connection "server-admin" --hostname new-server.example.com --port 3390 --force
uv run python guac_vm_manager.py edit --connection "vm-.*-rdp" --username newuser --password newpass --no-wol
uv run python guac_vm_manager.py edit --connection ".*" --hostname 192.168.1.100  # Bulk update all connections

# Delete connections and groups with regex pattern matching
uv run python guac_vm_manager.py delete --connection "old-server" --force
uv run python guac_vm_manager.py delete --connection "temp-.*" --group "Legacy.*" --force
uv run python guac_vm_manager.py delete --all  # ⚠️  DANGER: Deletes everything!

# Other commands
uv run python guac_vm_manager.py autogroup       # Smart connection grouping
uv run python guac_vm_manager.py test-auth       # Test API authentication
uv run python guac_vm_manager.py test-network "aa:bb:cc:dd:ee:ff"  # Test network scanning
uv run python guac_vm_manager.py debug-vms       # Debug VM discovery
uv run python guac_vm_manager.py interactive     # Full interactive menu
# Default (no command) runs interactive mode
```
uv run python guac_vm_manager.py delete --group "Legacy Servers" --force
uv run python guac_vm_manager.py delete --all  # ⚠️  DANGER: Deletes everything!

# Other commands
uv run python guac_vm_manager.py autogroup       # Smart connection grouping
uv run python guac_vm_manager.py test-auth       # Test API authentication
uv run python guac_vm_manager.py test-network "aa:bb:cc:dd:ee:ff"  # Test network scanning
uv run python guac_vm_manager.py debug-vms       # Debug VM discovery
uv run python guac_vm_manager.py interactive     # Full interactive menu
# Default (no command) runs interactive mode
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

**Force Recreation:**
```bash
uv run python guac_vm_manager.py auto --force
```
![Force Auto-Process Output](assets/auto-force-output.svg)

**External Host Addition:**
```bash
uv run python guac_vm_manager.py add-external
```
![Add External Output](assets/add-external-output.svg)

**Interactive Menu:**
```bash
uv run python guac_vm_manager.py
```
![Interactive Menu Output](assets/interactive-menu-output.svg)

**Maintenance:**
```bash
uv run python guac_vm_manager.py list
# Shows sync status: OK, password mismatch, port change, etc.
# Run with --verbose for detailed connection parameters
```

### Interactive Deletion Example
```bash
$ uv run python guac_vm_manager.py delete
```

![Interactive Delete Output](assets/interactive-delete-output.svg)

**Features:**
- Visual selection with checkboxes (`[x]` for selected, `[ ]` for unselected)
- Navigate with arrow keys, select/deselect with spacebar
- Confirmation dialog requires typing "DELETE" to proceed
- Safe cancellation with ESC or Ctrl+C

<details>
<summary><strong>⚙ Technical Details (API Integration & Network Discovery)</strong></summary>

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

</details>

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

<details>
<summary><strong>◦ Development & Contributing</strong></summary>

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

## Contributing

1. Fork repository
2. Create feature branch
3. Add tests for new functionality  
4. Ensure no hardcoded credentials in commits
5. Submit pull request

</details>

<details>
<summary><strong>⚠ Troubleshooting</strong></summary>

### Common Issues
- **Authentication failures**: Verify API credentials and endpoint URLs
- **No VMs discovered**: Check Proxmox token permissions and network connectivity
- **IP detection fails**: Ensure tool runs on same network as VMs, check guest agent status
- **Connection creation fails**: Verify Guacamole admin permissions and data source configuration

### Debug Commands
```bash
# Test both API connections
uv run python guac_vm_manager.py test-auth
```
![Authentication Test Output](assets/test-auth-output.svg)

```bash
# Show detailed VM information
uv run python guac_vm_manager.py --debug-vms
```
![Debug VMs Output](assets/debug-vms-output.svg)

```bash
# Test network discovery
uv run python guac_vm_manager.py test-network "vm:mac:address"
```
![Network Discovery Output](assets/network-discovery-output.svg)

</details>

## License

MIT License - See LICENSE file for details

## Continuous Integration (CI)

A GitHub Actions workflow is included at `.github/workflows/ci.yml`. It runs on pushes and pull requests to `main` and performs lightweight checks to keep the repository healthy.

Workflow summary:
- Matrix: Python 3.10, 3.11, 3.12
- Installs runtime dependencies from `requirements.txt`
- Runs `scripts/check_no_emojis.py` to enforce the repository's no-emoji policy
- Compiles Python files to catch syntax errors (`python -m compileall`)
 - Static analysis (linting) is delegated to DeepSource (see `.deepsource.toml`)

Run the same checks locally (macOS / zsh):

```bash
# create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

# run emoji policy check
python scripts/check_no_emojis.py

# syntax check
python -m compileall -q .

Note: linting and static analysis are performed by DeepSource using the
configuration in `.deepsource.toml`. To enable DeepSource for this repository,
connect the repo at https://deepsource.io and use the existing `.deepsource.toml`.
```

If you'd like, I can add additional CI workflows (coverage reporting, automated releases, or security scans) as follow-ups.