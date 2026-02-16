"""Utilities for obfuscating IP addresses and hostnames for privacy."""

import ipaddress
import socket
from typing import Optional


def obfuscate_hostname(hostname: str, ip_address: Optional[str] = None) -> str:
    """
    Obfuscate a hostname or IP address for display.

    For IPs with reverse DNS: obfuscates all labels except last 2, first label partially masked
    For IPs without reverse DNS or "unknown": shows IP with last two octets obfuscated
    For hostnames: obfuscates all labels except last 2, first label partially masked

    Args:
        hostname: The hostname or IP address to obfuscate
        ip_address: Optional IP address for reverse DNS lookup

    Returns:
        Obfuscated hostname/IP address
    """
    if not hostname:
        return "***"

    # If hostname is "unknown", treat it as IP address lookup
    if hostname == "unknown" and ip_address:
        hostname = ip_address

    # Check if it's an IP address
    is_ip = _is_ip_address(hostname)
    ip_to_check = ip_address if ip_address else (hostname if is_ip else None)

    if ip_to_check:
        # Try reverse DNS lookup
        try:
            reverse_dns = socket.gethostbyaddr(ip_to_check)[0]
            parts = reverse_dns.split('.')
            if len(parts) >= 4:
                # Obfuscate first label and middle, keep last 2
                middle_count = len(parts) - 3
                obfuscated_middle = ['***'] * middle_count
                return f"{_obfuscate_part(parts[0])}.{'.'.join(obfuscated_middle)}.{parts[-2]}.{parts[-1]}"
            elif len(parts) == 3:
                return f"{_obfuscate_part(parts[0])}.{_obfuscate_part(parts[1])}.{parts[2]}"
            elif len(parts) == 2:
                return f"{_obfuscate_part(parts[0])}.{parts[1]}"
            else:
                return _obfuscate_part(reverse_dns)
        except (socket.herror, socket.gaierror, OSError):
            # No reverse DNS - obfuscate IP
            if is_ip or ip_to_check:
                if _is_ipv6(ip_to_check):
                    addr = ipaddress.ip_address(ip_to_check)
                    exploded = addr.exploded
                    segments = exploded.split(':')
                    return f"{segments[0]}:{segments[1]}:*:*:*:*:*:*"
                else:
                    octets = ip_to_check.split('.')
                    if len(octets) == 4:
                        return f"{octets[0]}.{octets[1]}.*.*"
                    return ip_to_check

    # Check if hostname is an IPv6 address that wasn't handled above
    if _is_ipv6(hostname):
        addr = ipaddress.ip_address(hostname)
        exploded = addr.exploded
        segments = exploded.split(':')
        return f"{segments[0]}:{segments[1]}:*:*:*:*:*:*"

    # Regular hostname obfuscation
    parts = hostname.split('.')
    if len(parts) >= 4:
        middle_count = len(parts) - 3
        obfuscated_middle = ['***'] * middle_count
        return f"{_obfuscate_part(parts[0])}.{'.'.join(obfuscated_middle)}.{parts[-2]}.{parts[-1]}"
    elif len(parts) == 3:
        return f"{_obfuscate_part(parts[0])}.{_obfuscate_part(parts[1])}.{parts[2]}"
    elif len(parts) == 2:
        return f"{_obfuscate_part(parts[0])}.{parts[1]}"
    else:
        return _obfuscate_part(hostname)


def _obfuscate_part(part: str) -> str:
    """Obfuscate a single hostname part."""
    if len(part) <= 2:
        return "**"
    return part[:2] + "*" * min(3, len(part) - 2)


def _is_ip_address(value: str) -> bool:
    """Check if a string is an IP address (IPv4 or IPv6)."""
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, AttributeError):
        return False


def _is_ipv6(value: str) -> bool:
    """Check if a string is an IPv6 address."""
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
    except (ValueError, AttributeError):
        return False
