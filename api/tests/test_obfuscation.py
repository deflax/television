# pyright: reportImplicitRelativeImport=false

import sys
import unittest
from pathlib import Path

api_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(api_dir))

from utils.obfuscation import obfuscate_hostname


class ObfuscationTest(unittest.TestCase):
    def test_ipv6_cidr_display_key_is_obfuscated_with_prefix(self):
        self.assertEqual(
            obfuscate_hostname('2a01:5a8:302:59c0::/64', '2a01:5a8:302:59c0::/64'),
            '2a01:05a8:0302:*:*:*:*:*/64',
        )

    def test_ipv4_cidr_display_key_is_obfuscated_with_prefix(self):
        self.assertEqual(
            obfuscate_hostname('203.0.113.0/24', '203.0.113.0/24'),
            '203.0.*.*/24',
        )


if __name__ == '__main__':
    _ = unittest.main()
