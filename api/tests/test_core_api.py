import importlib.util
import unittest
from pathlib import Path
from typing import Protocol


class CoreAPIClientType(Protocol):
    def v3_process_get_config(self, id: str) -> dict[str, object]: ...


def load_core_api_client():
    module_path = Path(__file__).resolve().parents[1] / 'services' / 'core_api.py'
    spec = importlib.util.spec_from_file_location('core_api_under_test', module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CoreAPIClient


CoreAPIClient = load_core_api_client()


class DummyResponse:
    def __init__(self, body: dict[str, object]):
        self.body: dict[str, object] = body

    def json(self) -> dict[str, object]:
        return self.body


class CoreAPIClientTest(unittest.TestCase):
    def test_v3_process_get_config_uses_process_config_endpoint(self):
        client = CoreAPIClient('https://core.example.test', 'user', 'pass')
        requests: list[tuple[str, str, dict[str, object]]] = []
        expected_body: dict[str, object] = {'input': [{'address': 'http://example.test/live'}]}

        def fake_request(method: str, path: str, **kwargs: object) -> DummyResponse:
            requests.append((method, path, kwargs))
            return DummyResponse(expected_body)

        client._request = fake_request

        body = client.v3_process_get_config('restreamer-ui:ingest:channel-current')

        self.assertEqual(body, expected_body)
        self.assertEqual(
            requests,
            [('GET', '/api/v3/process/restreamer-ui:ingest:channel-current/config', {})],
        )


if __name__ == '__main__':
    unittest.main()
