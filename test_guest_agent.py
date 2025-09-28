#!/usr/bin/env python3

import sys
sys.path.append('.')

from config import Config
from guac_vm_manager import ProxmoxAPI

def test_vm_network_info(vmid: int):
    """Test guest agent network info for a specific VM"""
    config = Config()
    proxmox_api = ProxmoxAPI(config)
    
    print(f"Testing VM {vmid} network info...")
    
    # Get network details
    network_details = proxmox_api.get_vm_network_info('pve', vmid)
    
    print(f"Found {len(network_details)} network interfaces:")
    for i, interface in enumerate(network_details):
        print(f"\nInterface {i+1}:")
        print(f"  Config Interface: {interface.get('interface', 'N/A')}")
        print(f"  Guest Interface: {interface.get('guest_interface', 'N/A')}")
        print(f"  MAC: {interface.get('mac', 'N/A')}")
        print(f"  Model: {interface.get('model', 'N/A')}")
        print(f"  Bridge: {interface.get('bridge', 'N/A')}")
        
        ip_addresses = interface.get('ip_addresses', [])
        print(f"  IP Addresses ({len(ip_addresses)}):")
        for addr in ip_addresses:
            ip_addr = addr.get('ip-address') or addr.get('address')
            prefix = addr.get('prefix', 'N/A')
            print(f"    - {ip_addr}/{prefix}")
            
    return network_details

if __name__ == "__main__":
    # Test with arch-desktop (VM 106) - should be running
    test_vm_network_info(106)