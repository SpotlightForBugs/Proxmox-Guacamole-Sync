# Proxmox-Guacamole Sync

A Python tool that automates the creation and management of Apache Guacamole connections for Proxmox VMs. This tool bridges the gap between Proxmox VE and Apache Guacamole, allowing you to automatically create remote connections (RDP, VNC, SSH) based on VM credentials stored in Proxmox VM notes.

## Features

- • **Multi-Protocol Support**: RDP, VNC, and SSH connections
- • **VM Notes Integration**: Store credentials and settings in Proxmox VM notes
- • **Wake-on-LAN Support**: Automatically wake VMs before connecting
- • **Flexible Configuration**: Override settings per connection via VM notes
- • **Connection Grouping**: Organize connections into groups for better management
- • **Smart Updates**: Detect and update existing connections when VM details change
- • **Duplicate Prevention**: Intelligent handling of existing connections
- • **Customizable Settings**: Per-connection RDP and WoL settings
- • **Modern CLI Interface**: Typer-based CLI with Rich output formatting
- • **PVE Source Tracking**: Track which Proxmox node each connection was created from
- • **Enhanced Hostname Resolution**: Shows both hostname and IP when available
- • **Clean Authentication**: Silent endpoint probing with professional status display

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

The tool now features a modern Typer-based CLI with rich output and clean authentication:

```bash
# Interactive mode - pick a VM and set it up
uv run python guac_vm_manager.py add

# List connections with PVE source tracking and hostname resolution
uv run python guac_vm_manager.py list

# Test authentication with beautiful, clean output
uv run python guac_vm_manager.py test-auth

# Interactive menu (default when no command given)
uv run python guac_vm_manager.py
```

### Other Commands

```bash
# List existing connections with PVE source tracking
uv run python guac_vm_manager.py list

# Test API authentication (clean, beautiful output)
uv run python guac_vm_manager.py test-auth

# Test network scanning for specific MAC
uv run python guac_vm_manager.py test-network "aa:bb:cc:dd:ee:ff"

# Debug VM listing with full API response
uv run python guac_vm_manager.py debug-vms

# Auto-process all VMs with credentials in notes
uv run python guac_vm_manager.py auto

# Force recreate all connections
uv run python guac_vm_manager.py auto --force

# Interactive menu mode (default when no command specified)
uv run python guac_vm_manager.py interactive
```

## How it works

1. **Pick a VM** - Shows you all VMs from Proxmox with their status
2. **Find the IP** - Scans your network to find where the VM actually is  
3. **Get credentials** - Reads them from VM notes (much better than hardcoded passwords)
4. **Create connections** - Sets up RDP/VNC in Guacamole with Wake-on-LAN
5. **Group them** - Multiple users per VM get organized in a folder

If the VM is stopped and not on the network, it can start it for you and wait for it to boot up.

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