import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from obfuscation import obfuscate_hostname as _obfuscate_hostname


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
        Obfuscate a hostname or IP address for display.

        Args:
            hostname: The hostname or IP address to obfuscate
            ip_address: Optional IP address for reverse DNS lookup

        Returns:
            Obfuscated hostname/IP address
        """
        return _obfuscate_hostname(hostname, ip_address)
