import importlib.util
import logging
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

try:
    from quart import Quart
except ImportError:
    Quart = None


API_DIR = Path(__file__).resolve().parents[1]


class ScriptSourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            source = dict(attrs).get('src')
            if source:
                self.sources.append(source)


@unittest.skipIf(Quart is None, 'Quart is required for frontend asset tests')
class FrontendAssetVersionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.templates = root / 'templates'
        self.assets = root / 'static'
        self.templates.mkdir()
        (self.assets / 'js').mkdir(parents=True)
        self.script = self.assets / 'js' / 'player.js'
        self.script.write_text('window.playbackMode = "legacy";', encoding='utf-8')
        (self.templates / 'index_mux.html').write_text(
            '<script src="{{ static_asset_url(\'js/player.js\') }}"></script>',
            encoding='utf-8',
        )
        sys.path.insert(0, str(API_DIR))
        self.addCleanup(sys.path.remove, str(API_DIR))
        # Load independently of tests that replace web.frontend_routes in sys.modules.
        spec = importlib.util.spec_from_file_location('frontend_asset_routes', API_DIR / 'web' / 'frontend_routes.py')
        self.routes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.routes)

    def create_app(self):
        from web.state import WebRouteState
        from web.timecode_manager import TimecodeManager
        from web.visitor_tracker import VisitorTracker

        app = Quart(__name__, template_folder=str(self.templates), static_folder=str(self.assets))
        app.secret_key = 'asset-version-test'
        config = SimpleNamespace(server_name='localhost', core_hostname='localhost', archive_enabled=False, rec_path=str(self.assets))
        state = WebRouteState(timecode_manager=TimecodeManager(), visitor_tracker=VisitorTracker())
        self.routes.register_frontend_routes(app, config, SimpleNamespace(content=logging.getLogger('test.assets')), state)
        return app

    async def script_url(self, app):
        response = await app.test_client().get('/')
        self.assertEqual(response.status_code, 200)
        parser = ScriptSourceParser()
        parser.feed(await response.get_data(as_text=True))
        return parser.sources[0]

    async def test_changed_script_gets_a_new_cache_key_on_deployment(self):
        previous_url = await self.script_url(self.create_app())
        self.script.write_text('window.playbackMode = "current";', encoding='utf-8')
        current_url = await self.script_url(self.create_app())
        self.assertNotEqual(previous_url, current_url)
        self.assertEqual(previous_url.split('?', 1)[0], current_url.split('?', 1)[0])

    async def test_unchanged_script_keeps_its_cache_key_across_requests_and_restarts(self):
        app = self.create_app()
        first_url = await self.script_url(app)
        self.assertEqual(await self.script_url(app), first_url)
        self.assertEqual(await self.script_url(self.create_app()), first_url)


if __name__ == '__main__':
    unittest.main()
