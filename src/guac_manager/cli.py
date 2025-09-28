#!/usr/bin/env python3
"""
Guacamole VM Manager CLI using Cleo framework.
"""

import sys
import os
from pathlib import Path
from cleo import Application, Command
from cleo.helpers import argument, option

# Add the project root to the path so we can import the legacy module
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import from the legacy module
try:
    from guac_vm_manager import (
        Config, ProxmoxAPI, NetworkScanner,
        interactive_add_vm, list_connections, 
        auto_process_all_vms
    )
except ImportError as e:
    print(f"Error importing from guac_vm_manager: {e}")
    print("Please ensure the guac_vm_manager.py file is in the project root.")
    sys.exit(1)


class AddCommand(Command):
    """
    Add a new VM connection to Guacamole.

    add
    """

    name = "add"
    description = "Add new VM connection to Guacamole"

    def handle(self):
        """Handle the add command."""
        self.line("Adding new VM connection...")
        try:
            interactive_add_vm()
        except KeyboardInterrupt:
            self.line("\nOperation cancelled by user.")
            return 1
        except Exception as e:
            self.line_error(f"Error adding VM: {e}")
            return 1
        return 0


class ListCommand(Command):
    """
    List existing Guacamole connections.

    list
    """

    name = "list"
    description = "List existing Guacamole connections"

    def handle(self):
        """Handle the list command."""
        self.line("Listing existing connections...")
        try:
            list_connections()
        except Exception as e:
            self.line_error(f"Error listing connections: {e}")
            return 1
        return 0


class TestAuthCommand(Command):
    """
    Test Proxmox API authentication.

    test-auth
    """

    name = "test-auth"
    description = "Test Proxmox API authentication"

    def handle(self):
        """Handle the test-auth command."""
        self.line("Testing Proxmox API authentication...")
        try:
            config = Config()
            proxmox_api = ProxmoxAPI(config)
            proxmox_api.test_auth()
        except Exception as e:
            self.line_error(f"Authentication test failed: {e}")
            return 1
        return 0


class DebugVmsCommand(Command):
    """
    Debug VM listing with full response.

    debug-vms
    """

    name = "debug-vms"
    description = "Debug VM listing with full response"

    def handle(self):
        """Handle the debug-vms command."""
        self.line("Debugging VM listing...")
        try:
            config = Config()
            proxmox_api = ProxmoxAPI(config)
            nodes = proxmox_api.get_nodes()
            
            for node in nodes:
                node_name = node['node']
                
                # Check QEMU VMs
                qemu_url = f"{config.proxmox_base_url}/nodes/{node_name}/qemu"
                qemu_response = proxmox_api.session.get(qemu_url)
                self.line(f"Node: {node_name}")
                self.line(f"QEMU VMs URL: {qemu_url}")
                self.line(f"QEMU Status: {qemu_response.status_code}")
                self.line(f"QEMU Response: {qemu_response.text}")
                
                # Check LXC containers
                lxc_url = f"{config.proxmox_base_url}/nodes/{node_name}/lxc"
                lxc_response = proxmox_api.session.get(lxc_url)
                self.line(f"LXC Containers URL: {lxc_url}")
                self.line(f"LXC Status: {lxc_response.status_code}")
                self.line(f"LXC Response: {lxc_response.text}")
                self.line("-" * 50)
                
        except Exception as e:
            self.line_error(f"Error debugging VMs: {e}")
            return 1
        return 0


class TestNetworkCommand(Command):
    """
    Test network scanning for specific MAC address.

    test-network
        {mac : MAC address to search for}
    """

    name = "test-network"
    description = "Test network scanning for specific MAC address"
    arguments = [
        argument("mac", "MAC address to search for")
    ]

    def handle(self):
        """Handle the test-network command."""
        mac_address = self.argument("mac")
        self.line(f"Testing network scanning for MAC: {mac_address}")
        
        try:
            result = NetworkScanner.find_mac_on_network(mac_address)
            if result:
                self.line(f"Found: IP {result['ip']}, Hostname: {result['hostname']}")
            else:
                self.line("Not found")
        except Exception as e:
            self.line_error(f"Network scan failed: {e}")
            return 1
        return 0


class AutoCommand(Command):
    """
    Auto-process all VMs with credentials in notes.

    auto
        {--force : Force mode - recreate all connections and overwrite duplicates}
    """

    name = "auto"
    description = "Auto-process all VMs with credentials in notes"
    options = [
        option("force", "f", "Force mode - recreate all connections and overwrite duplicates", flag=True)
    ]

    def handle(self):
        """Handle the auto command."""
        force_mode = self.option("force")
        action = "force processing" if force_mode else "processing"
        self.line(f"Auto {action} all VMs with credentials...")
        
        try:
            auto_process_all_vms(force=force_mode)
        except KeyboardInterrupt:
            self.line("\nOperation cancelled by user.")
            return 1
        except Exception as e:
            self.line_error(f"Error in auto processing: {e}")
            return 1
        return 0


class WebCommand(Command):
    """
    Start the web interface (Streamlit app).

    web
        {--port=8501 : Port to run the web interface on}
        {--host=localhost : Host to bind the web interface to}
    """

    name = "web"
    description = "Start the web interface (Streamlit app)"
    options = [
        option("port", "p", "Port to run the web interface on", default="8501"),
        option("host", None, "Host to bind the web interface to", default="localhost")
    ]

    def handle(self):
        """Handle the web command."""
        port = self.option("port")
        host = self.option("host")
        
        self.line(f"Starting web interface on {host}:{port}...")
        
        try:
            import subprocess
            # Run streamlit app
            cmd = [
                sys.executable, "-m", "streamlit", "run", 
                "streamlit_app.py",
                "--server.port", str(port),
                "--server.address", host,
                "--server.headless", "true"
            ]
            
            # Change to the project root directory
            project_root = Path(__file__).parent.parent.parent
            os.chdir(project_root)
            
            self.line(f"Running: {' '.join(cmd)}")
            subprocess.run(cmd)
            
        except KeyboardInterrupt:
            self.line("\nWeb interface stopped by user.")
            return 0
        except FileNotFoundError:
            self.line_error("Streamlit not found. Install with: pip install streamlit")
            return 1
        except Exception as e:
            self.line_error(f"Error starting web interface: {e}")
            return 1
        return 0


class InteractiveCommand(Command):
    """
    Start interactive menu mode.

    interactive
    """

    name = "interactive"
    description = "Start interactive menu mode"

    def handle(self):
        """Handle the interactive command."""
        self.line("Guacamole VM Manager - Interactive Mode")
        self.line("=" * 40)
        
        try:
            while True:
                self.line("\nSelect an option:")
                self.line("1. Add VM to Guacamole")
                self.line("2. List existing connections")
                self.line("3. Auto-process all VMs with credentials")
                self.line("4. Exit")
                
                choice = input("\nEnter choice (1-4): ").strip()
                
                if choice == "1":
                    interactive_add_vm()
                elif choice == "2":
                    list_connections()
                elif choice == "3":
                    auto_process_all_vms(force=False)
                elif choice == "4":
                    self.line("Goodbye!")
                    break
                else:
                    self.line("Invalid choice. Please enter 1-4.")
                    
        except KeyboardInterrupt:
            self.line("\nExiting...")
            return 0
        except Exception as e:
            self.line_error(f"Unexpected error: {e}")
            return 1
        return 0


def create_application():
    """Create and configure the Cleo application."""
    app = Application("guac-manager", "1.0.0")
    app.set_catch_exceptions(False)
    
    # Add commands
    app.add(AddCommand())
    app.add(ListCommand())
    app.add(TestAuthCommand())
    app.add(DebugVmsCommand())
    app.add(TestNetworkCommand())
    app.add(AutoCommand())
    app.add(WebCommand())
    app.add(InteractiveCommand())
    
    return app


def main():
    """Main entry point for the CLI."""
    app = create_application()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
