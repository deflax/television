import importlib.util
import sys
import types
import unittest
from pathlib import Path

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportAny=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false, reportUnusedParameter=false

api_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(api_dir))


class FakeIcyResponse:
    def __init__(self, headers: dict[str, str], payload: bytes):
        self.headers = headers
        self.status_code = 200
        self._payload = payload
        self.raw = types.SimpleNamespace(read=self._read)

    def _read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload)
        result = self._payload[:size]
        self._payload = self._payload[size:]
        return result

    def iter_content(self, chunk_size: int = 1):
        for index in range(0, len(self._payload), chunk_size):
            yield self._payload[index : index + chunk_size]

    def raise_for_status(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type: object | None, exc: object | None, tb: object | None) -> bool:
        return False


def load_icy_metadata_module():
    requests_module = types.ModuleType('requests')

    class RequestException(Exception):
        pass

    def fake_get(*args: object, **kwargs: object) -> None:
        return None

    setattr(requests_module, 'RequestException', RequestException)
    setattr(requests_module, 'get', fake_get)

    original_requests = sys.modules.get('requests')
    sys.modules['requests'] = requests_module
    try:
        module_path = api_dir / 'services' / 'icy_metadata.py'
        if not module_path.exists():
            raise ModuleNotFoundError("No module named 'services.icy_metadata'")

        spec = importlib.util.spec_from_file_location('services.icy_metadata_under_test', module_path)
        if spec is None or spec.loader is None:
            raise ModuleNotFoundError("No module named 'services.icy_metadata'")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original_requests is None:
            sys.modules.pop('requests', None)
        else:
            sys.modules['requests'] = original_requests


def build_metadata_payload(title: str | None) -> bytes:
    audio_prefix = b'A' * 16
    if title is None:
        metadata = b''
    else:
        metadata = f"StreamTitle='{title}';".encode('utf-8')

    block_size = len(metadata)
    if block_size % 16 != 0:
        metadata += b'\x00' * (16 - (block_size % 16))

    return audio_prefix + bytes([len(metadata) // 16]) + metadata


class IcyMetadataTest(unittest.TestCase):
    def test_read_icy_stream_title_returns_title_for_valid_metadata_block(self):
        module = load_icy_metadata_module()
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_get(url: str, **kwargs):
            calls.append((url, kwargs))
            return FakeIcyResponse(
                headers={'icy-metaint': '16'},
                payload=build_metadata_payload('Artist - Track'),
            )

        original_get = module.requests.get
        try:
            module.requests.get = fake_get
            result = module.read_icy_stream_title('https://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get

        self.assertEqual(result, 'Artist - Track')
        self.assertEqual(
            calls,
            [
                (
                    'https://example.test/live',
                    {'headers': {'Icy-MetaData': '1'}, 'stream': True, 'timeout': 3.5},
                ),
            ],
        )

    def test_read_icy_stream_title_returns_none_for_empty_metadata_block(self):
        module = load_icy_metadata_module()

        def fake_get(url: str, **kwargs):
            return FakeIcyResponse(
                headers={'icy-metaint': '16'},
                payload=build_metadata_payload(None),
            )

        original_get = module.requests.get
        try:
            module.requests.get = fake_get
            result = module.read_icy_stream_title('https://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get

        self.assertIsNone(result)

    def test_read_icy_stream_title_returns_none_when_icy_metaint_header_is_missing(self):
        module = load_icy_metadata_module()

        def fake_get(url: str, **kwargs):
            return FakeIcyResponse(headers={}, payload=b'')

        original_get = module.requests.get
        try:
            module.requests.get = fake_get
            result = module.read_icy_stream_title('https://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get

        self.assertIsNone(result)

    def test_read_icy_stream_title_returns_none_for_unsupported_non_http_source_url(self):
        module = load_icy_metadata_module()

        def fake_get(*args, **kwargs):
            raise AssertionError('requests.get should not be called for non-http URLs')

        original_get = module.requests.get
        try:
            module.requests.get = fake_get
            result = module.read_icy_stream_title('ftp://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get

        self.assertIsNone(result)

    def test_read_icy_stream_title_returns_none_on_request_error(self):
        module = load_icy_metadata_module()

        def fake_get(url: str, **kwargs):
            raise module.requests.RequestException('boom')

        original_get = module.requests.get
        try:
            module.requests.get = fake_get
            result = module.read_icy_stream_title('https://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get

        self.assertIsNone(result)


if __name__ == '__main__':
    _ = unittest.main()
