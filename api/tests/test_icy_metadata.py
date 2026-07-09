import importlib.util
import sys
import types
import unittest
from pathlib import Path

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportAny=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false, reportUnusedParameter=false

api_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(api_dir))


class FakeIcyResponse:
    def __init__(self, headers: dict[str, str], payload: bytes, max_read_size: int | None = None):
        self.headers = headers
        self.status_code = 200
        self._payload = payload
        self._max_read_size = max_read_size
        self.raw = types.SimpleNamespace(read=self._read)

    def _read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload)
        if self._max_read_size is not None:
            size = min(size, self._max_read_size)
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


class FakeSocket:
    def __init__(self, payload: bytes, max_read_size: int | None = None):
        self._payload = payload
        self._max_read_size = max_read_size
        self.sent: bytes = b''
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        _ = timeout

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        if self._max_read_size is not None:
            size = min(size, self._max_read_size)
        result = self._payload[:size]
        self._payload = self._payload[size:]
        return result

    def close(self) -> None:
        self.closed = True


def build_socket_response(payload: bytes, metaint: int = 16, name: str = 'Example ICY stream') -> bytes:
    return (
        b'HTTP/1.0 200 OK\r\n' +
        f'icy-name: {name}\r\n'.encode('ascii') +
        f'icy-metaint: {metaint}\r\n'.encode('ascii') +
        b'\r\n' +
        payload
    )


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
        sys.modules['services.icy_metadata_under_test'] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop('services.icy_metadata_under_test', None)
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


def build_empty_then_title_payload(title: str) -> bytes:
    return b'A' * 16 + b'\x00' + build_metadata_payload(title)


class IcyMetadataTest(unittest.TestCase):
    def test_read_icy_stream_title_returns_title_for_valid_metadata_block(self):
        module = load_icy_metadata_module()
        fake_socket = FakeSocket(build_socket_response(build_metadata_payload('Artist - Track')))

        def fake_get(url: str, **kwargs):
            raise AssertionError('requests.get should not be called when socket ICY parsing succeeds')

        def fake_create_connection(address: tuple[str, int], timeout: float):
            self.assertEqual(address, ('example.test', 80))
            self.assertEqual(timeout, 3.5)
            return fake_socket

        original_get = module.requests.get
        original_create_connection = module.socket.create_connection
        try:
            module.requests.get = fake_get
            module.socket.create_connection = fake_create_connection
            result = module.read_icy_stream_title('http://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get
            module.socket.create_connection = original_create_connection

        self.assertEqual(result, 'Artist - Track')
        self.assertIn(b'Icy-MetaData: 1', fake_socket.sent)

    def test_read_icy_stream_title_handles_partial_socket_reads(self):
        module = load_icy_metadata_module()
        fake_socket = FakeSocket(
            build_socket_response(build_metadata_payload('Zeal Litta - Dark Shadows (Original Mix)')),
            max_read_size=5,
        )

        def fake_get(url: str, **kwargs):
            raise AssertionError('requests.get should not be called when socket ICY parsing succeeds')

        def fake_create_connection(address: tuple[str, int], timeout: float):
            _ = address
            _ = timeout
            return fake_socket

        original_get = module.requests.get
        original_create_connection = module.socket.create_connection
        try:
            module.requests.get = fake_get
            module.socket.create_connection = fake_create_connection
            result = module.read_icy_stream_title('http://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get
            module.socket.create_connection = original_create_connection

        self.assertEqual(result, 'Zeal Litta - Dark Shadows (Original Mix)')

    def test_read_icy_stream_title_removes_domain_noise_from_title(self):
        module = load_icy_metadata_module()
        for raw_title, expected_title in (
            ('Artist - Track - radio.example.com | https://stream.example.net/live', 'Artist - Track'),
            ('Brickman - Field.Wind * amoris.example.test', 'Brickman - Field.Wind'),
        ):
            fake_socket = FakeSocket(build_socket_response(build_metadata_payload(raw_title)))
            original_create_connection = module.socket.create_connection
            try:
                module.socket.create_connection = lambda address, timeout: fake_socket
                result = module.read_icy_stream_title('http://example.test/live', timeout=3.5)
            finally:
                module.socket.create_connection = original_create_connection

            self.assertEqual(result, expected_title)

    def test_read_icy_stream_title_scans_past_empty_metadata_block(self):
        module = load_icy_metadata_module()
        fake_socket = FakeSocket(build_socket_response(
            build_empty_then_title_payload('Second Block Artist - Second Block Track')
        ))

        def fake_get(url: str, **kwargs):
            raise AssertionError('requests.get should not be called when socket ICY parsing succeeds')

        def fake_create_connection(address: tuple[str, int], timeout: float):
            _ = address
            _ = timeout
            return fake_socket

        original_get = module.requests.get
        original_create_connection = module.socket.create_connection
        try:
            module.requests.get = fake_get
            module.socket.create_connection = fake_create_connection
            result = module.read_icy_stream_title('http://example.test/live', timeout=3.5)
        finally:
            module.requests.get = original_get
            module.socket.create_connection = original_create_connection

        self.assertEqual(result, 'Second Block Artist - Second Block Track')

    def test_read_icy_stream_title_handles_icy_status_line_streams(self):
        module = load_icy_metadata_module()
        fake_socket = FakeSocket(
            b'ICY 200 OK\r\n' +
            b'icy-name: Example old ICY stream\r\n' +
            b'icy-metaint: 16\r\n' +
            b'\r\n'
            + build_metadata_payload('Example Artist - Example Track')
        )

        def fake_get(url: str, **kwargs):
            raise module.requests.RequestException('ICY status line')

        def fake_create_connection(address: tuple[str, int], timeout: float):
            self.assertEqual(address, ('old-icy.example.test', 8000))
            self.assertEqual(timeout, 3.5)
            return fake_socket

        original_get = module.requests.get
        original_create_connection = module.socket.create_connection
        try:
            module.requests.get = fake_get
            module.socket.create_connection = fake_create_connection
            result = module.read_icy_stream_title('http://old-icy.example.test:8000/', timeout=3.5)
        finally:
            module.requests.get = original_get
            module.socket.create_connection = original_create_connection

        self.assertEqual(result, 'Example Artist - Example Track')
        self.assertIn(b'Icy-MetaData: 1', fake_socket.sent)
        self.assertTrue(fake_socket.closed)

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
