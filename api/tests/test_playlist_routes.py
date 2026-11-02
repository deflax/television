import importlib.util
import json
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


API_DIR = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, API_DIR / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schedule = load_module('guide_schedule_under_test', 'services/schedule.py')


def load_routes(name, path):
    quart = types.ModuleType('quart')
    quart.request = types.SimpleNamespace(headers={'Host': 'TV.Example:8080', 'X-Forwarded-Proto': 'https'},
                                          host='internal', scheme='http')
    helpers = types.ModuleType('web.helpers')
    helpers.get_client_address = lambda request: '127.0.0.1'
    state = types.ModuleType('web.state')
    state.HLSViewerSession = object
    state.WebRouteState = object
    state.hls_viewer_display_key = lambda value: value
    state.update_hls_viewer_display_state = lambda *args: None
    with patch.dict(sys.modules, {'quart': quart, 'services.schedule': schedule, 'web.helpers': helpers, 'web.state': state}):
        return load_module(name, path)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 1, 23, 30, tzinfo=timezone.utc)


class FakeApp:
    def __init__(self):
        self.routes = {}

    def route(self, path, methods):
        def register(function):
            self.routes[path] = function
            return function
        return register

    async def make_response(self, body):
        return types.SimpleNamespace(body=body, headers={})

    def before_serving(self, function):
        return function


class GuideRoutesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FakeApp()
        self.logger = types.SimpleNamespace(info=lambda message: None)
        self.loggers = types.SimpleNamespace(content=self.logger, sse=self.logger)
        self.manager = types.SimpleNamespace(catalogue={
            'offline': {'name': 'Offline & scheduled', 'start_at': '08', 'details': '<Episode>'},
            'monday': {'name': 'Monday only', 'start_at': '12', 'days': ['mon']},
            'disabled': {'name': 'Disabled', 'start_at': 'never'},
            'excluded': {'name': 'Manual Monday', 'start_at': 'now', 'days': ['mon']},
            'offline-live': {'name': 'Offline live', 'start_at': 'now'},
        }, database={
            'live': {'name': 'Live', 'start_at': 'now'},
            'excluded': {'name': 'Manual Monday', 'start_at': 'now', 'days': ['mon']},
        }, playhead={'id': 'excluded', 'name': 'Manual Monday'})
        self.routes = load_routes('playlist_routes_under_test', 'web/playlist_routes.py')
        self.routes.register_playlist_routes(self.app, self.manager, self.loggers)

    async def test_xml_uses_seven_utc_dates_and_offline_catalogue(self):
        with patch.object(self.routes, 'datetime', FixedDatetime):
            response = await self.app.routes['/epg.xml']()
        self.assertEqual(response.headers['Content-Type'], 'application/xml; charset=utf-8')
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')
        self.assertTrue(response.body.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">'))
        tv = ET.fromstring(response.body)
        self.assertEqual(tv.attrib['generator-info-url'], 'https://TV.Example:8080')
        self.assertEqual(tv.find('channel').attrib['id'], 'tv.example')
        self.assertEqual(tv.find('channel/icon').attrib['src'], 'https://TV.Example:8080/static/images/logo.png')
        programmes = tv.findall('programme')
        self.assertEqual(len(programmes), 9)
        offline = [p for p in programmes if p.findtext('title') == 'Offline & scheduled']
        self.assertEqual(len(offline), 7)
        self.assertEqual(offline[0].attrib['start'], '20261101080000 +0000')
        self.assertEqual(offline[-1].attrib['start'], '20261107080000 +0000')
        self.assertEqual(offline[0].findtext('desc'), '<Episode>')
        monday = [p for p in programmes if p.findtext('title') == 'Monday only']
        self.assertEqual(monday[0].attrib['start'], '20261102120000 +0000')
        live = [p for p in programmes if p.findtext('title') == 'Live']
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].attrib['start'], '20261101233000 +0000')
        self.assertEqual(live[0].attrib['stop'], '20261102000000 +0000')
        for programme in programmes:
            self.assertEqual(programme.attrib['channel'], 'tv.example')
            self.assertNotIn(programme.findtext('title'), ('Disabled', 'Manual Monday', 'Offline live'))

    async def test_empty_runtime_does_not_hide_catalogue(self):
        self.manager.database = {}
        with patch.object(self.routes, 'datetime', FixedDatetime):
            response = await self.app.routes['/epg.xml']()
        self.assertEqual(len(ET.fromstring(response.body).findall('programme')), 8)

    async def test_playlist_preserves_public_host_and_scheme(self):
        response = await self.app.routes['/live.m3u8']()
        self.assertIn('url-tvg="https://TV.Example:8080/epg.xml"', response.body)
        self.assertIn('tvg-id="tv.example"', response.body)
        self.assertIn('https://TV.Example:8080/live/stream.m3u8', response.body)
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')

    async def test_sse_initial_guide_keeps_manual_playhead_labels(self):
        routes = load_routes('epg_sse_routes_under_test', 'web/sse_routes.py')
        state = types.SimpleNamespace(
            sse_clients=set(), recent_sse_disconnects={}, hls_viewer_count=0,
            visitor_tracker=types.SimpleNamespace(connect=lambda ip: None, disconnect=lambda ip: None),
        )
        routes.register_sse_routes(self.app, self.manager, self.loggers, None, state)
        with patch.object(routes, 'datetime', FixedDatetime):
            response = await self.app.routes['/events']()
            events = response.body
            try:
                await anext(events)  # playhead
                await anext(events)  # visitors
                event = await anext(events)
            finally:
                await events.aclose()
        self.assertTrue(event.startswith('event: epg\ndata: '))
        payload = json.loads(event.split('data: ', 1)[1])
        self.assertEqual(payload['start_day'], '2026-11-01')
        self.assertEqual(payload['days'], 7)
        self.assertEqual(payload['catalogue']['excluded']['name'], 'Manual Monday')
        self.assertEqual(payload['catalogue']['live']['name'], 'Live')
        self.assertEqual(len(payload['programmes']), 9)
        self.assertNotIn('excluded', [p['id'] for p in payload['programmes']])
        self.assertTrue(all(p['start'].endswith('+00:00') for p in payload['programmes']))
        self.assertEqual(response.headers['Content-Type'], 'text/event-stream')
        self.assertEqual(state.sse_clients, set())


if __name__ == '__main__':
    unittest.main()
