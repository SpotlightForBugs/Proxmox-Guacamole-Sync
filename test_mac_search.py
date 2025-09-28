#!/usr/bin/env python3

import sys
sys.path.append('.')

from guac_vm_manager import NetworkScanner

def test_specific_mac():
    """Test the exact MAC that's causing issues"""
    target_mac = "BC:24:11:43:92:2A"
    
    print(f"Testing MAC: {target_mac}")
    
    # Use the actual NetworkScanner method
    result = NetworkScanner.find_mac_on_network(target_mac)
    
    if result:
        print(f"Result: {result}")
    else:
        print("No result found (correct behavior)")

if __name__ == "__main__":
    test_specific_mac()