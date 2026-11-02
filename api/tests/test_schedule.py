import importlib.util
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    'schedule_under_test', Path(__file__).resolve().parents[1] / 'services' / 'schedule.py'
)
assert spec is not None and spec.loader is not None
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


class ChannelSettingsTest(unittest.TestCase):
    def test_json_and_legacy_literals_normalize_days(self):
        for description in (
            '{"start_at":"1730","days":["MON","fri","Mon"],"prio":3,"details":"Show"}',
            "{'start_at': '1730', 'days': ['MON', 'fri', 'Mon'], 'prio': 3, 'details': 'Show'}",
        ):
            with self.subTest(description=description):
                parsed = schedule.parse_channel_settings(description)
                self.assertEqual(parsed['days'], ['mon', 'fri'])
                self.assertEqual(parsed['prio'], 3)
                self.assertEqual(parsed['details'], 'Show')

    def test_absent_days_remain_absent(self):
        self.assertEqual(schedule.parse_channel_settings('{"start_at":"now"}'), {'start_at': 'now', 'prio': 0})
        self.assertEqual(schedule.parse_channel_settings("{'start_at': 'never'}"), {'start_at': 'never', 'prio': 0})

    def test_invalid_settings_raise_value_error(self):
        descriptions = [
            '[]', 'null', 'not a dictionary', '{}', '{"start_at":"2500"}',
            '{"start_at":"2360"}', '{"start_at":"930"}',
            '{"start_at":"12","days":[]}', '{"start_at":"12","days":"mon"}',
            '{"start_at":"12","days":["monday"]}', '{"start_at":"12","days":[1]}',
            '{"start_at":"12","days":null}', '{"start_at":"12","prio":true}',
            '{"start_at":"12","prio":1.5}', '{"start_at":"12","prio":"1"}',
        ]
        for description in descriptions:
            with self.subTest(description=description), self.assertRaises(ValueError):
                schedule.parse_channel_settings(description)

    def test_supported_military_times(self):
        for value, expected in [('0', (0, 0)), ('14', (14, 0)), ('0030', (0, 30)), ('2359', (23, 59))]:
            with self.subTest(value=value):
                self.assertEqual(schedule.parse_military_time(value), expected)
        for value in ['', None, 'now', 'never', '24', '2400', '123', '12345', '-1', '12:30']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                schedule.parse_military_time(value)


class ProgrammeScheduleTest(unittest.TestCase):
    def test_weekday_filtering_rolls_from_sunday_into_next_week(self):
        catalogue = {
            'daily': {'name': 'Daily', 'start_at': '08'},
            'sunday': {'name': 'Sunday', 'start_at': '10', 'days': ['sun']},
            'monday': {'name': 'Monday', 'start_at': '12', 'days': ['mon']},
            'never': {'name': 'Disabled', 'start_at': 'never'},
            'now': {'name': 'Live', 'start_at': 'now'},
        }
        programmes = schedule.programmes_for_days(catalogue, date(2026, 11, 1), 7)
        self.assertEqual(len(programmes), 9)
        self.assertEqual([p['start'].date() for p in programmes if p['id'] == 'daily'],
                         [date(2026, 11, 1) + timedelta(days=i) for i in range(7)])
        self.assertEqual([p['start'].date() for p in programmes if p['id'] == 'sunday'], [date(2026, 11, 1)])
        self.assertEqual([p['start'].date() for p in programmes if p['id'] == 'monday'], [date(2026, 11, 2)])
        self.assertTrue(all(p['id'] not in ('now', 'never') for p in programmes))
        for programme in programmes:
            midnight = datetime.combine(programme['start'].date() + timedelta(days=1), datetime.min.time(), timezone.utc)
            self.assertEqual(programme['start'].tzinfo, timezone.utc)
            self.assertLess(programme['start'], programme['stop'])
            self.assertLessEqual(programme['stop'], midnight)

    def test_equal_starts_share_next_strictly_later_boundary(self):
        catalogue = {
            'b': {'name': 'B', 'start_at': '08', 'prio': 0},
            'a': {'name': 'A', 'start_at': '0800', 'prio': 1},
            'c': {'name': 'C', 'start_at': '2359'},
        }
        programmes = schedule.programmes_for_days(catalogue, date(2026, 11, 2), 1)
        self.assertEqual([p['id'] for p in programmes], ['a', 'b', 'c'])
        self.assertEqual(programmes[0]['stop'], programmes[2]['start'])
        self.assertEqual(programmes[1]['stop'], programmes[2]['start'])
        self.assertEqual(programmes[2]['stop'], datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertEqual(programmes, schedule.programmes_for_days(dict(reversed(list(catalogue.items()))), date(2026, 11, 2), 1))

    def test_last_programme_stops_at_midnight_not_next_scheduled_day(self):
        programmes = schedule.programmes_for_days({
            'weekly': {'name': 'Weekly', 'start_at': '12', 'days': ['mon', 'fri']},
        }, date(2026, 11, 2), 7)
        self.assertEqual(len(programmes), 2)
        self.assertTrue(all(p['stop'] - p['start'] == timedelta(hours=12) for p in programmes))

    def test_stopped_catalogue_entries_are_in_guide(self):
        programmes = schedule.programmes_for_guide({
            'offline': {'name': 'Stopped', 'start_at': '07'},
            'offline-live': {'name': 'Stopped live', 'start_at': 'now'},
        }, {}, datetime(2026, 11, 2, 20, tzinfo=timezone.utc))
        self.assertEqual(len(programmes), 7)
        self.assertTrue(all(p['id'] == 'offline' for p in programmes))
        self.assertEqual(programmes[0]['start'].hour, 7)
        self.assertEqual(programmes[0]['start'].date(), date(2026, 11, 2))

    def test_live_is_runtime_only_weekday_filtered_and_midnight_capped(self):
        catalogue = {
            'excluded': {'name': 'Tuesday only', 'start_at': 'now', 'days': ['tue']},
            'offline': {'name': 'Offline', 'start_at': 'now'},
        }
        database = {
            'live': {'name': 'Live', 'start_at': 'now'},
            'excluded': {'name': 'Tuesday only', 'start_at': 'now'},
            'never': {'name': 'Disabled', 'start_at': 'never'},
        }
        for hour, expected_stop in [(12, datetime(2026, 11, 2, 15, tzinfo=timezone.utc)),
                                    (23, datetime(2026, 11, 3, tzinfo=timezone.utc))]:
            now = datetime(2026, 11, 2, hour, tzinfo=timezone.utc)
            with self.subTest(hour=hour):
                programmes = schedule.programmes_for_guide(catalogue, database, now)
                self.assertEqual(len(programmes), 1)
                self.assertEqual(programmes[0]['id'], 'live')
                self.assertTrue(programmes[0]['live'])
                self.assertEqual(programmes[0]['start'], now)
                self.assertEqual(programmes[0]['stop'], expected_stop)


if __name__ == '__main__':
    unittest.main()
