import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


class TimecodeManager:
    """Manages timecode generation and validation based on hostname."""
    
    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize TimecodeManager with a secret key.
        
        Args:
            secret_key: Secret key for HMAC. If None, generates from environment or random.
        """
        if secret_key is None:
            secret_key = os.environ.get('TIMECODE_SECRET_KEY')
            if secret_key is None:
                # Generate a random key if not set (should be set in production)
                secret_key = secrets.token_hex(32)
        self.secret_key = secret_key.encode() if isinstance(secret_key, str) else secret_key
    
    def generate_timecode(self, hostname: str) -> str:
        """
        Generate a timecode based on hostname and current time.
        
        The timecode is valid for 24 hours and is generated using HMAC-SHA256.
        
        Args:
            hostname: The visitor's hostname
            
        Returns:
            A timecode string (hex digest)
        """
        # Get current date (changes every 24 hours)
        current_date = datetime.now(timezone.utc).date().isoformat()
        
        # Create message: hostname + date
        message = f"{hostname}:{current_date}".encode()
        
        # Generate HMAC
        hmac_obj = hmac.new(self.secret_key, message, hashlib.sha256)
        timecode = hmac_obj.hexdigest()[:6]  # Use first 6 characters for readability
        
        return timecode
    
    def validate_timecode(self, hostname: str, timecode: str) -> bool:
        """
        Validate a timecode for a given hostname.
        
        Args:
            hostname: The visitor's hostname
            timecode: The timecode to validate
            
        Returns:
            True if timecode is valid, False otherwise
        """
        # Try current date
        current_date = datetime.now(timezone.utc).date().isoformat()
        expected_timecode = self.generate_timecode_for_date(hostname, current_date)
        
        if hmac.compare_digest(timecode, expected_timecode):
            return True
        
        # Try previous date (in case of timezone edge cases)
        previous_date = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        expected_timecode = self.generate_timecode_for_date(hostname, previous_date)
        
        return hmac.compare_digest(timecode, expected_timecode)
    
    def generate_timecode_for_date(self, hostname: str, date: str) -> str:
        """
        Generate timecode for a specific date (used for validation).
        
        Args:
            hostname: The visitor's hostname
            date: ISO format date string
            
        Returns:
            A timecode string
        """
        message = f"{hostname}:{date}".encode()
        hmac_obj = hmac.new(self.secret_key, message, hashlib.sha256)
        return hmac_obj.hexdigest()[:6]
    
    def obfuscate_hostname(self, hostname: str, ip_address: Optional[str] = None) -> str:
        """
        Obfuscate a hostname or IP address for display in Discord.

        For IPs with reverse DNS: shows first part and last 2 parts, obfuscates middle parts
        For IPs without reverse DNS or "unknown": shows IP with last two octets obfuscated
        For hostnames: obfuscates the middle parts

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
        is_ip = self._is_ip_address(hostname)
        ip_to_check = ip_address if ip_address else (hostname if is_ip else None)

        if ip_to_check:
            # Try reverse DNS lookup
            try:
                reverse_dns = socket.gethostbyaddr(ip_to_check)[0]
                # Obfuscate reverse DNS: show first part and last 2 parts
                # Example: clients-pools.pl.cooolbox.bg -> clients-pools.***.cooolbox.bg
                parts = reverse_dns.split('.')
                if len(parts) >= 4:
                    # Keep first part and last 2 parts, obfuscate middle
                    middle_count = len(parts) - 3
                    obfuscated_middle = ['***'] * middle_count
                    return f"{parts[0]}.{'.'.join(obfuscated_middle)}.{parts[-2]}.{parts[-1]}"
                elif len(parts) == 3:
                    # For 3 parts, keep first and last, obfuscate middle
                    return f"{parts[0]}.{self._obfuscate_part(parts[1])}.{parts[2]}"
                elif len(parts) == 2:
                    # For 2 parts, partially obfuscate first part
                    return f"{self._obfuscate_part(parts[0])}.{parts[1]}"
                else:
                    return self._obfuscate_part(reverse_dns)
            except (socket.herror, socket.gaierror, OSError):
                # No reverse DNS - obfuscate IP
                if is_ip or ip_to_check:
                    if self._is_ipv6(ip_to_check):
                        # For IPv6, show first 2 segments, obfuscate rest
                        # e.g., 2001:db8:85a3::8a2e:370:7334 -> 2001:db8:*:*:*:*:*:*
                        addr = ipaddress.ip_address(ip_to_check)
                        # Get exploded form to have all 8 segments
                        exploded = addr.exploded
                        segments = exploded.split(':')
                        return f"{segments[0]}:{segments[1]}:*:*:*:*:*:*"
                    else:
                        # IPv4: show first two octets, hide last two
                        octets = ip_to_check.split('.')
                        if len(octets) == 4:
                            return f"{octets[0]}.{octets[1]}.*.*"
                        return ip_to_check
                # Fall through to hostname obfuscation

        # Check if hostname is an IPv6 address that wasn't handled above
        if self._is_ipv6(hostname):
            addr = ipaddress.ip_address(hostname)
            exploded = addr.exploded
            segments = exploded.split(':')
            return f"{segments[0]}:{segments[1]}:*:*:*:*:*:*"

        # Regular hostname obfuscation
        parts = hostname.split('.')
        if len(parts) >= 4:
            # Keep first part and last 2 parts, obfuscate middle
            middle_count = len(parts) - 3
            obfuscated_middle = ['***'] * middle_count
            return f"{parts[0]}.{'.'.join(obfuscated_middle)}.{parts[-2]}.{parts[-1]}"
        elif len(parts) == 3:
            # For 3 parts, keep first and last, obfuscate middle
            return f"{parts[0]}.{self._obfuscate_part(parts[1])}.{parts[2]}"
        elif len(parts) == 2:
            # Obfuscate the main part, keep TLD visible
            return f"{self._obfuscate_part(parts[0])}.{parts[1]}"
        else:
            # Single part hostname
            return self._obfuscate_part(hostname)

    def _obfuscate_part(self, part: str) -> str:
        """
        Obfuscate a single hostname part.

        Args:
            part: The hostname part to obfuscate

        Returns:
            Obfuscated part
        """
        if len(part) <= 2:
            return "**"
        return part[:2] + "*" * min(3, len(part) - 2)
    
    def _is_ip_address(self, value: str) -> bool:
        """
        Check if a string is an IP address (IPv4 or IPv6).
        
        Args:
            value: String to check
            
        Returns:
            True if value is an IP address, False otherwise
        """
        try:
            ipaddress.ip_address(value)
            return True
        except (ValueError, AttributeError):
            return False
    
    def _is_ipv6(self, value: str) -> bool:
        """
        Check if a string is an IPv6 address.
        
        Args:
            value: String to check
            
        Returns:
            True if value is an IPv6 address, False otherwise
        """
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
        except (ValueError, AttributeError):
            return False
