"""
Network utilities for Wake-on-LAN and network scanning.

This module provides utilities for network discovery and Wake-on-LAN functionality.
"""

import ipaddress
import re
import socket
import subprocess
from typing import Dict, List, Optional


class NetworkScanner:
    """Network scanning functionality to find MAC addresses."""

    @staticmethod
    def get_local_network_range() -> Optional[str]:
        """
        Get the local network range (e.g., 192.168.1.0/24).

        Returns:
            Network range string or None if cannot be determined
        """
        try:
            # Get default gateway on macOS/Linux
            result = subprocess.run(
                ["route", "-n", "get", "default"], capture_output=True, text=True, timeout=10
            )

            gateway_match = re.search(r"gateway: (\d+\.\d+\.\d+\.\d+)", result.stdout)
            if not gateway_match:
                return None

            gateway = gateway_match.group(1)
            # Assume /24 network for simplicity
            network_parts = gateway.split(".")
            network_base = ".".join(network_parts[:3]) + ".0/24"
            return network_base
        except Exception:
            return None

    @staticmethod
    def scan_arp_table(target_mac: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Scan ARP table for MAC addresses.

        Args:
            target_mac: Optional specific MAC to search for

        Returns:
            List of ARP entries or single entry if target_mac found
        """
        arp_entries = []
        try:
            # Try faster arp command first
            result = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=2)
            if result.returncode != 0:
                # Fallback to regular arp -a
                result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=3)

            for line in result.stdout.split("\n"):
                # Parse ARP entries - handle multiple formats
                match = re.search(r"(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([a-fA-F0-9:]+)", line)
                if match:
                    hostname, ip, mac = match.groups()

                    # Validate MAC address format
                    mac_parts = mac.lower().split(":")
                    if len(mac_parts) != 6:
                        continue

                    # Normalize MAC address with leading zeros
                    try:
                        mac_normalized = ":".join(part.zfill(2) for part in mac_parts)
                        # Validate each part is valid hex
                        for part in mac_parts:
                            int(part, 16)
                    except ValueError:
                        continue

                    entry = {
                        "hostname": hostname if hostname != "?" else ip,
                        "ip": ip,
                        "mac": mac_normalized,
                    }

                    # If looking for specific MAC, check match
                    if target_mac:
                        target_parts = target_mac.lower().replace("-", ":").split(":")
                        target_normalized = ":".join(part.zfill(2) for part in target_parts)

                        if mac_normalized == target_normalized:
                            return [entry]  # Return immediately if found
                    else:
                        arp_entries.append(entry)

        except Exception:
            pass

        # If looking for specific MAC and we reach here, it wasn't found
        if target_mac:
            return []

        return arp_entries

    @staticmethod
    def ping_sweep_network(network_range: str) -> None:
        """
        Ping sweep to populate ARP table.

        Args:
            network_range: Network range in CIDR notation (e.g., 192.168.1.0/24)
        """
        try:
            network = ipaddress.IPv4Network(network_range, strict=False)

            # Ping a range of IPs to populate ARP table
            processes = []
            for ip in list(network.hosts())[:50]:  # Limit to first 50 hosts
                try:
                    proc = subprocess.Popen(
                        ["ping", "-c", "1", "-W", "1000", str(ip)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    processes.append(proc)
                except Exception:
                    continue

            # Wait for pings to complete
            for proc in processes:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

        except Exception:
            pass

    @staticmethod
    def find_mac_on_network(target_mac: str) -> Optional[Dict[str, str]]:
        """
        Find a specific MAC address on the local network.

        Args:
            target_mac: MAC address to search for

        Returns:
            Dict with hostname, ip, and mac if found, None otherwise
        """
        # First check ARP table
        entries = NetworkScanner.scan_arp_table(target_mac)
        if entries:
            return entries[0]

        # If not found, do network sweep and try again
        network_range = NetworkScanner.get_local_network_range()
        if network_range:
            NetworkScanner.ping_sweep_network(network_range)

            # Check ARP table again after sweep
            entries = NetworkScanner.scan_arp_table(target_mac)
            if entries:
                return entries[0]

        return None


class WakeOnLan:
    """Wake-on-LAN functionality."""

    @staticmethod
    def send_wol_packet(
        mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9
    ) -> bool:
        """
        Send Wake-on-LAN magic packet.

        Args:
            mac_address: Target MAC address
            broadcast_ip: Broadcast IP address
            port: UDP port for WoL packet

        Returns:
            True if packet sent successfully, False otherwise
        """
        sock = None
        try:
            # Remove any separators from MAC address
            mac_address = mac_address.replace(":", "").replace("-", "").replace(".", "")

            if len(mac_address) != 12:
                raise ValueError("MAC address must be 12 hex characters")

            # Convert MAC address to bytes
            mac_bytes = bytes.fromhex(mac_address)

            # Create magic packet: 6 bytes of 0xFF followed by 16 repetitions of MAC address
            magic_packet = b"\xff" * 6 + mac_bytes * 16

            # Send packet
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic_packet, (broadcast_ip, port))

            return True

        except Exception:
            return False
        finally:
            if sock:
                sock.close()

    @staticmethod
    def validate_mac_address(mac_address: str) -> bool:
        """
        Validate MAC address format.

        Args:
            mac_address: MAC address to validate

        Returns:
            True if valid, False otherwise
        """
        # Remove separators
        clean_mac = mac_address.replace(":", "").replace("-", "").replace(".", "")

        # Check if it's 12 hex characters
        if len(clean_mac) != 12:
            return False

        try:
            int(clean_mac, 16)
            return True
        except ValueError:
            return False
