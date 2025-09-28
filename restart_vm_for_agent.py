#!/usr/bin/env python3

import sys
sys.path.append('.')

from config import Config
from guac_vm_manager import ProxmoxAPI

def restart_vm_for_agent(vmid: int):
    """Restart VM to enable guest agent communication"""
    config = Config()
    proxmox_api = ProxmoxAPI(config)
    
    print(f"Restarting VM {vmid} to enable guest agent...")
    
    # Stop VM
    print("Stopping VM...")
    if proxmox_api.stop_vm('pve', vmid):
        print("VM stopped successfully")
        
        # Wait a moment
        import time
        print("Waiting 3 seconds...")
        time.sleep(3)
        
        # Start VM  
        print("Starting VM...")
        if proxmox_api.start_vm('pve', vmid):
            print("VM started successfully")
            print("Wait about 30 seconds for the guest agent to initialize, then test again")
        else:
            print("Failed to start VM")
    else:
        print("Failed to stop VM")

if __name__ == "__main__":
    restart_vm_for_agent(106)