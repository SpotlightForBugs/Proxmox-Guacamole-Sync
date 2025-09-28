#!/usr/bin/env python3
import re
import subprocess

def debug_mac_matching():
    """Debug MAC address matching in ARP table"""
    target_mac = "BC:24:11:43:92:2A"
    
    print(f"Target MAC: {target_mac}")
    
    # Normalize target MAC
    target_parts = target_mac.lower().replace('-', ':').split(':')
    target_normalized = ':'.join(part.zfill(2) for part in target_parts)
    print(f"Target normalized: {target_normalized}")
    
    # Get ARP table
    result = subprocess.run(['arp', '-an'], capture_output=True, text=True, timeout=2)
    
    print(f"\nChecking ARP table entries:")
    for line in result.stdout.split('\n'):
        match = re.search(r'(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([a-fA-F0-9:]+)', line)
        if match:
            hostname, ip, mac = match.groups()
            
            # Normalize found MAC
            mac_parts = mac.lower().split(':')
            if len(mac_parts) == 6:
                try:
                    mac_normalized = ':'.join(part.zfill(2) for part in mac_parts)
                    
                    # Check if this matches target
                    is_match = mac_normalized == target_normalized
                    
                    print(f"ARP Entry: {mac} -> {mac_normalized} -> {ip} {'*** MATCH ***' if is_match else ''}")
                    
                    if is_match:
                        print(f"FOUND MATCH: {mac_normalized} at {ip}")
                        
                except ValueError:
                    print(f"Invalid MAC: {mac}")

if __name__ == "__main__":
    debug_mac_matching()