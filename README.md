# Proxmox-Guacamole Sync

Automated Apache Guacamole connection management from Proxmox (and external hosts) using credentials stored in VM notes.

## Features

Core
* RDP / VNC / SSH provisioning
* Single-file tool (Typer + Rich)
* VM notes parsing with flexible key order & multi-protocol support
* Template variables: {vmname} {user} {proto} {port} {vmid} {node} {ip} {hostname}
* Automatic encrypted password handling (Fernet) + migration of plain pass -> encrypted

Discovery & Network
* **IPv4-only networking** - Completely filters out IPv6 addresses for clean connections
* **Loopback interface filtering** - Ignores unnecessary loopback pseudo-interfaces  
* Guest agent IP + fallback ARP/ping sweep
* Wake-on-LAN (built-in, no deps)
* Auto-start stopped VM for discovery then restore prior state

Synchronization
* Out-of-sync detection (hostname, username, password, port, protocol, WoL)
* Interactive remediation: update / recreate / ignore / pull from Guacamole → notes (bidirectional)
* Structured credential line auto-append if only free-form notes exist
* Optional external host onboarding (not in Proxmox)

User Experience
* **Interactive deletion mode** - Select connections/groups with spacebar, confirm with "DELETE"
* Progress indicators for VM fetch, updates, connection creation
* Early credential apply / ignore / edit prompt
* Inline credential editor (username, protocol, port, name)
* Connection grouping per VM (only when >1 connection)
* Rich tables with node, status, memory, sync issues
* PVE source tracking for each connection

Safety & Consistency
* Duplicate + location (parent group) reconciliation
* Selective WoL disable per credential line
* Non-destructive VM notes updates (preserve arbitrary text)
* Idempotent re-runs (no duplicate connections)

CLI
* add / auto / list / **delete** / test-auth / test-network / interactive / add-external
* Auto-skip interactive when non-TTY / tests / CI
* Rich help text with comprehensive feature descriptions

## VM Notes Credential Line

```
user:"admin" pass:"Password" protos:"rdp,vnc,ssh" rdp_port:"3390" vnc_port:"5901" confName:"{vmname}-{user}-{proto}";
user:"viewer" pass:"readonly" protos:"vnc" vnc_settings:"read-only=true,color-depth=16,encoding=tight";
```

Multiple lines allowed. Parameters may appear in any order. Use `encrypted_password:` instead of `pass:` for encrypted values.

### VNC-Specific Settings
- `vnc_settings:"key=value,key2=value2"` - Protocol-specific configuration
- Common VNC options: `color-depth` (8/16/24/32), `encoding` (tight/raw/hextile), `read-only` (true/false), `cursor` (local/remote)

Key tokens (aliases accepted):
* user / username
* pass / password / encrypted_password
* protos / protocols / proto (single)
* confName / connection_name / default_conf_name
* rdp_port / vnc_port / ssh_port / port
* rdp_settings / rdpSettings
* wol_settings / wolSettings
* wol_disabled / wolDisabled

## Minimal Usage

```bash
# Interactive VM selection and setup
uv run python guac_vm_manager.py add

# Auto-process all VMs with credentials
uv run python guac_vm_manager.py auto

# List all connections with clean IPv4-only display
uv run python guac_vm_manager.py list

# Interactive delete mode (select with spacebar)
uv run python guac_vm_manager.py delete

# Add external (non-Proxmox) hosts
uv run python guac_vm_manager.py add-external
```

## Encryption Migration
Plain `pass:` lines are detected and converted to `encrypted_password:` during processing; original notes retained with structured lines appended if missing.

## External Hosts
`add-external` creates connections without Proxmox dependency (manual hostname + credentials).

## Sync Pull (Guac -> Notes)
When differences are detected you can choose `g` to rebuild structured credential lines from current Guacamole connection state.

## Requirements
* Python 3.8+
* Guacamole API access
* Proxmox API token (for VM mode)

## Run
```
cp config_example.py config.py
uv sync
uv run python guac_vm_manager.py
```

## Exit Codes
* 0 success / no new work
* 1 authentication or API failure
* >1 unexpected runtime error

## Notes
No emojis permitted (repository policy). All updates are additive and avoid overwriting free-form notes content.

## Installation

### Prerequisites

- Python 3.7+
- UV package manager (recommended)
- Access to Proxmox VE API
- Access to Apache Guacamole API

### Install UV (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Clone and Setup

```bash
git clone https://github.com/SpotlightForBugs/Proxmox-Guacamole-Sync.git
cd Proxmox-Guacamole-Sync
```

### Install Dependencies with UV

```bash
# Install dependencies
uv sync

# Or create a virtual environment and install
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
./setup.sh  # Installs uv and dependencies
```
```

### Configuration

Create a `.env` file in the project directory:

```bash
# Proxmox Configuration
PROXMOX_HOST=your-proxmox-host.com
PROXMOX_USERNAME=root@pam
PROXMOX_PASSWORD=your-proxmox-password

# Guacamole Configuration
GUACAMOLE_HOST=your-guacamole-host.com
GUACAMOLE_USERNAME=guacadmin
GUACAMOLE_PASSWORD=your-guac-password
```

## VM Notes Format

Store connection credentials in your Proxmox VM notes using this format:

### New Flexible Syntax

**[NEW] One user per line with multiple protocols and arbitrary parameter order!**

```
user:"username" pass:"password" protos:"protocol1,protocol2" [port_overrides] [settings];
```

### Supported Protocols

- **rdp**: Remote Desktop Protocol (default port 3389)
- **vnc**: Virtual Network Computing (default port 5900)  
- **ssh**: Secure Shell (default port 22)

### Available Parameters (any order)

**Recommended parameter names:**
- `username:"admin"` or `user:"admin"` - **Required**: Username for connections
- `password:"pass123"` or `pass:"pass123"` - **Required**: Password for connections
- `encrypted_password:"gAAAAAB..."` - **Alternative**: Encrypted password (see encryption section)
- `protocols:"rdp,vnc,ssh"` or `protos:"rdp,vnc,ssh"` - **Required**: Protocols (comma-separated)
- `connection_name:"{vmname}-{user}-{protocol}"` or `confName:"{vmname}-{user}-{proto}"` - Connection name template
- `rdp_port:"3390"` - Custom RDP port (overrides default 3389)
- `vnc_port:"5901"` - Custom VNC port (overrides default 5900) 
- `ssh_port:"2222"` - Custom SSH port (overrides default 22)
- `port:"1234"` - Port for single protocol (when using `protocol:` instead of `protocols:`)
- `rdp_settings:"key=val"` or `rdpSettings:"key=val"` - RDP-specific settings
- `wol_settings:"key=val"` or `wolSettings:"key=val"` - Wake-on-LAN settings
- `wol_disabled:"true"` or `wolDisabled:"true"` - Disable Wake-on-LAN for all protocols

**Backward compatibility**: Old abbreviated parameter names (`user`, `pass`, `protos`, `confName`, etc.) are still fully supported.

### Examples

#### Single User, Multiple Protocols
```
user:"admin" pass:"SecurePass123" protos:"rdp,ssh" confName:"{vmname}-{user}-{proto}";
```
*Creates: Windows-admin-rdp and Windows-admin-ssh*

#### Custom Ports for Each Protocol  
```
user:"sysadmin" pass:"AdminPass" protos:"rdp,vnc,ssh" rdp_port:"3390" vnc_port:"5901" ssh_port:"2222";
```
*Creates connections with custom ports instead of defaults*

#### Single Protocol with Settings
```
user:"developer" pass:"DevPass" protos:"rdp" rdpSettings:"color-depth=32,enable-wallpaper=true" wolDisabled:"true";
```

#### Arbitrary Parameter Order (all equivalent)
```
user:"test" protos:"ssh,vnc" pass:"TestPass123" ssh_port:"22" vnc_port:"5900";
protos:"rdp" user:"admin" rdpSettings:"color-depth=16" pass:"AdminPass";  
pass:"UserPass" user:"user" protos:"rdp,ssh" confName:"Custom-{proto}";
```

#### Backward Compatibility (old format still works)
```
user:"admin" pass:"AdminPass" proto:"rdp" confName:"{vmname}-admin";
```
*Note: Use `proto:` for single protocol, `protos:` for multiple*

#### Complex Multi-User Configuration
```
default_conf_name:"{vmname}-{user}-{proto}"

user:"administrator" pass:"WinAdminPass" protos:"rdp" rdpSettings:"color-depth=32";
user:"backup" pass:"BackupPass" protos:"rdp" rdp_port:"3390" confName:"Backup-{vmname}";  
user:"root" pass:"LinuxRootPass" protos:"ssh,vnc" ssh_port:"22" vnc_port:"5901" wolDisabled:"true";
user:"developer" pass:"DevPass" protos:"rdp,ssh,vnc" confName:"Dev-{proto}-{vmname}";
```

*Creates: Windows-administrator-rdp, Backup-Windows, Windows-root-ssh, Windows-root-vnc, Dev-rdp-Windows, Dev-ssh-Windows, Dev-vnc-Windows*

### Connection Name Templates

Use these placeholders in `confName` or `default_conf_name`:

- `{vmname}` - VM name
- `{user}` / `{username}` - Username
- `{password}` - Password (not recommended for security)
- `{proto}` / `{protocol}` - Protocol (rdp/vnc/ssh)
- `{port}` - Connection port number
- `{vmid}` / `{vm_id}` - VM ID number
- `{node}` / `{vm_node}` - Proxmox node name  
- `{ip}` / `{vm_ip}` - VM IP address
- `{hostname}` / `{host}` - Local hostname

#### Template Examples
```bash
"{vmname}-{user}-{proto}"           # → Windows-admin-rdp
"{user}@{vmname}:{port}"           # → admin@Windows:3389  
"{proto}-{vmname}-{node}"          # → rdp-Windows-pve1
"Custom-{user}-{proto}-{port}"     # → Custom-admin-ssh-2222
```

### RDP Settings

Available RDP settings for `rdpSettings`:

You can override RDP connection settings using `rdpSettings:"key=value,key=value"`:

**Common settings:**
- `enable-wallpaper` - Show desktop wallpaper (true/false)
- `enable-theming` - Use Windows themes (true/false) 
- `enable-font-smoothing` - Font smoothing (true/false)
- `color-depth` - Color depth (8, 16, 24, 32)
- `resolution` - Screen resolution (e.g., 1920x1080)
- `enable-full-window-drag` - Window dragging effects (true/false)

**Default settings** are optimized for good performance with visual quality enabled.

### Setting it up

1. Go to your VM in Proxmox web interface
2. Click **Notes** tab
3. Add your credential lines somewhere in the notes
4. Run the script - it'll find and use them automatically

The script automatically updates existing connections if passwords or settings change in VM notes.

## Configuration

### 1. Copy Configuration File

```bash
cp config_example.py config.py
```

### 2. Edit Configuration

Edit `config.py` with your specific settings:

```python
class Config:
    # Guacamole Configuration
    GUAC_BASE_URL = "https://your-guacamole-server.com"
    GUAC_USERNAME = "your-admin-username"
    GUAC_PASSWORD = "your-admin-password"
    GUAC_DATA_SOURCE = "mysql"
    
    # Proxmox Configuration  
    PROXMOX_HOST = "192.168.1.100"
    PROXMOX_PORT = 8006
    PROXMOX_TOKEN_ID = "root@pam!your-token-name"
    PROXMOX_SECRET = "your-generated-token-secret"
    
    
```

**Important**: The `config.py` file is git-ignored to protect your credentials.

### 3. Password Encryption (Optional)

For enhanced security, you can encrypt passwords stored in VM notes:

#### Step 1: Generate an encryption key
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

#### Step 2: Add the key to your config.py
```python
class Config:
    # ... other settings ...
    ENCRYPTION_KEY = "your_generated_32_character_base64_key_here"
```

#### Step 3: Encrypt passwords in VM notes
Use `encrypted_password:` instead of `pass:` in VM notes:

**Plain password:**
```
user:"admin" pass:"SecurePass123" protos:"rdp";
```

**Encrypted password:**
```
user:"admin" encrypted_password:"gAAAAABhZ..." protos:"rdp";
```

The tool can automatically encrypt existing plain passwords in VM notes. When it finds plain passwords, it will offer to encrypt them for you.

## Network Configuration

### IPv4-Only Mode
The tool now operates in **IPv4-only mode** for clean, reliable connections:

- **Automatic IPv4 Detection**: Prioritizes and uses only IPv4 addresses from VM network interfaces
- **IPv6 Filtering**: Completely ignores IPv6 addresses to prevent connection issues
- **Clean Connection Display**: Lists show clean IPv4 addresses like `192.168.1.100` instead of long IPv6 strings
- **Loopback Filtering**: Automatically ignores loopback pseudo-interfaces to reduce noise

### Benefits:
- **Simplified Network Management**: No confusion between IPv4/IPv6 addresses  
- **Reliable Connections**: Avoids IPv6 connectivity issues in mixed environments
- **Clean Output**: Connection lists are readable and professional
- **Faster Discovery**: Focuses scanning on usable IPv4 addresses only

This ensures all Guacamole connections use standard IPv4 addressing for maximum compatibility.

## Interactive Examples  

### Multi-Protocol User with Custom Ports

1. **Setup VM Notes in Proxmox:**
   ```
   user:"sysadmin" pass:"SecurePass123" protos:"rdp,ssh,vnc" rdp_port:"3390" ssh_port:"2222" confName:"{vmname}-{user}-{proto}:{port}";
   ```

2. **Run the tool:**
   ```bash
   uv run python guac_vm_manager.py --add
   ```

3. **Tool Output:**
   ```
    User: sysadmin, Protocol: RDP, Port: 3390
    Connection Name: Windows-sysadmin-rdp:3390
   
    User: sysadmin, Protocol: SSH, Port: 2222  
    Connection Name: Windows-sysadmin-ssh:2222
   
    User: sysadmin, Protocol: VNC, Port: 5900
    Connection Name: Windows-sysadmin-vnc:5900
   ```

4. **Result:**
   - Creates 3 connections for one user  
   - Custom ports for RDP and SSH
   - All organized in connection group

### Advanced Configuration with Settings

1. **VM Notes:**
   ```
   default_conf_name:"{user}@{vmname}-{proto}"
   
   user:"admin" pass:"AdminPass" protos:"rdp" rdpSettings:"color-depth=32,enable-wallpaper=false" wolDisabled:"true";
   user:"developer" pass:"DevPass" protos:"rdp,ssh" rdp_port:"3391" wolSettings:"udp-port=7";
   user:"backup" pass:"BackupPass" protos:"vnc" vnc_port:"5901" confName:"Backup-{vmname}";
   ```

2. **Tool Output:**
   ```
    User: admin, Protocol: RDP, Port: 3389
   RDP Settings: color-depth=32, enable-wallpaper=false
   WoL: Disabled
    Connection Name: admin@Windows-rdp
   
    User: developer, Protocol: RDP, Port: 3391
   WoL Settings: udp-port=7
    Connection Name: developer@Windows-rdp
   
    User: developer, Protocol: SSH, Port: 22
   WoL Settings: udp-port=7  
    Connection Name: developer@Windows-ssh
   
    User: backup, Protocol: VNC, Port: 5901
    Connection Name: Backup-Windows
   ```

### Parameter Order Flexibility

All these are equivalent and create the same connections:
```
user:"admin" pass:"pass123" protos:"rdp,ssh" ssh_port:"2222";
protos:"rdp,ssh" user:"admin" ssh_port:"2222" pass:"pass123";  
pass:"pass123" ssh_port:"2222" protos:"rdp,ssh" user:"admin";
```

## Usage

### Modern CLI Interface

The tool features a modern Typer-based CLI with rich output, IPv4-only networking, and interactive management:

```bash
# Interactive mode - pick a VM and set it up
uv run python guac_vm_manager.py add

# List connections with PVE source tracking and clean IPv4 display
uv run python guac_vm_manager.py list

# Interactive deletion - select multiple items with spacebar
uv run python guac_vm_manager.py delete

# Test authentication with beautiful, clean output  
uv run python guac_vm_manager.py test-auth

# Interactive menu (default when no command given)
uv run python guac_vm_manager.py
```

### All Commands

```bash
# List existing connections with PVE source tracking (IPv4 only)
uv run python guac_vm_manager.py list

# Interactive deletion mode - select with spacebar, confirm with "DELETE"
uv run python guac_vm_manager.py delete

# Test API authentication (clean, beautiful output)
uv run python guac_vm_manager.py test-auth

# Test network scanning for specific MAC
uv run python guac_vm_manager.py test-network "aa:bb:cc:dd:ee:ff"

# Debug VM listing with full API response
uv run python guac_vm_manager.py debug-vms

# Auto-process all VMs with credentials in notes
uv run python guac_vm_manager.py auto

# Force recreate all connections (IPv4 filtering applied)
uv run python guac_vm_manager.py auto --force

# Interactive menu mode (default when no command specified)
uv run python guac_vm_manager.py interactive
```

## Interactive Delete Mode

The new delete command provides a safe, interactive way to remove connections and groups:

```bash
uv run python guac_vm_manager.py delete
```

### Features:
- **Visual Selection**: Navigate with arrow keys, select/deselect with spacebar
- **Multi-Selection**: Select multiple connections and groups at once  
- **Clear Indication**: Selected items show `[x]` checkbox and count
- **Safety Confirmation**: Must type "DELETE" to confirm permanent removal
- **Mixed Deletion**: Delete connections and groups together in one operation
- **Cancel Anytime**: ESC or Ctrl+C to abort safely

### Usage:
1. Run `uv run python guac_vm_manager.py delete`
2. Use ↑/↓ arrow keys to navigate
3. Press SPACE to select/deselect items (shows `[x]` when selected)
4. Press ENTER when done selecting
5. Type "DELETE" to confirm permanent deletion
6. Or ESC/Ctrl+C to cancel

## How it works

1. **Pick a VM** - Shows you all VMs from Proxmox with their status
2. **Find the IP** - Automatically detects IPv4 addresses only (no IPv6 clutter)
3. **Get credentials** - Reads them from VM notes (much better than hardcoded passwords)
4. **Create connections** - Sets up RDP/VNC in Guacamole with Wake-on-LAN
5. **Group them** - Multiple users per VM get organized in a folder (single connections stay ungrouped)

The tool now filters out loopback interfaces and uses IPv4-only networking for clean, reliable connections. If the VM is stopped, it can start it for you and wait for it to boot up.

## Examples

**Add one VM interactively:**
```bash
uv run guac_vm_manager.py --add
```

**Batch process multiple VMs:**
```bash  
for vm_id in 100 101 102; do
    echo "$vm_id" | uv run guac_vm_manager.py --add -y
done
```

## Troubleshooting

**Can't authenticate with Proxmox?**
```bash
uv run guac_vm_manager.py --test-auth
```
Check your token has the right permissions and privilege separation is disabled.

**Wake-on-LAN not working?**  
Make sure pve-dosthol is installed and running on Proxmox. Check that the VM supports WoL in BIOS.

**Connections failing?**  
Verify the VM has RDP/VNC enabled and the ports aren't blocked by firewall.

## File Structure

```
guac-vm-manager/
├── guac_vm_manager.py    # Main script
├── config.py             # Your configuration (git-ignored)
├── config_example.py     # Configuration template  
├── requirements.txt      # Python dependencies
├── setup.sh             # Setup script
├── .gitignore           # Ignores config.py and common files
└── README.md            # This file
```

## Contributing

1. Fork the repository
2. Make your changes
3. Test thoroughly with your environment
4. Submit a pull request

## License

This project is open source. See LICENSE file for details.

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Verify all prerequisites are installed correctly  
3. Test individual components (auth, network scanning, etc.)
4. Review Guacamole and Proxmox logs for additional details