"""Validated channel settings and catalogue-based schedules, always in UTC."""

import ast
import json
from datetime import date, datetime, time, timedelta, timezone


WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def parse_military_time(time_str: str) -> tuple[int, int]:
    """Parse HHMM or a legacy hour-only time into a validated (hour, minute)."""
    value = str(time_str).strip()
    if not value.isascii() or not value.isdigit():
        raise ValueError(f'invalid schedule time {time_str!r}: expected HHMM or hour')
    if len(value) <= 2:
        hour, minute = int(value), 0
    elif len(value) == 4:
        hour, minute = int(value[:-2]), int(value[-2:])
    else:
        raise ValueError(f'invalid schedule time {time_str!r}: expected HHMM or hour')
    if hour > 23 or minute > 59:
        raise ValueError(f'invalid schedule time {time_str!r}: out of range')
    return hour, minute


def parse_channel_settings(description: str) -> dict:
    """Read JSON or legacy Python-literal settings without adding absent days.

    Explicit days must be a nonempty weekday list. Priority is an integer, not
    a boolean; omitted priority defaults to zero. Other channel fields survive.
    """
    try:
        settings = json.loads(description)
    except (TypeError, ValueError):
        try:
            settings = ast.literal_eval(description)
        except (TypeError, ValueError, SyntaxError) as exc:
            raise ValueError('invalid channel settings: expected a JSON or Python dictionary') from exc
    if not isinstance(settings, dict):
        raise ValueError('invalid channel settings: expected a dictionary')

    start_at = str(settings.get('start_at', '')).strip().lower()
    if start_at not in ('now', 'never'):
        parse_military_time(start_at)
    settings['start_at'] = start_at
    priority = settings.get('prio', 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValueError('invalid channel priority: expected an integer')
    settings['prio'] = priority

    if 'days' in settings:
        days = settings['days']
        if not isinstance(days, list) or not days:
            raise ValueError('invalid channel days: expected a nonempty weekday list')
        normalized = []
        for day in days:
            if not isinstance(day, str) or day.lower() not in WEEKDAYS:
                raise ValueError(f'invalid channel weekday {day!r}')
            day = day.lower()
            if day not in normalized:
                normalized.append(day)
        settings['days'] = normalized
    return settings


def programmes_for_days(catalogue: dict, start_day: date, count: int) -> list[dict]:
    """Build deterministic timed programmes, including stopped catalogue entries.

    Missing days means daily guide visibility, not permission to control ingest.
    Live (now) and disabled (never) entries do not recur. Equal starts share the
    same next strictly later boundary; every programme ends by UTC midnight.
    """
    programmes = []
    for offset in range(count):
        day = start_day + timedelta(days=offset)
        midnight = datetime.combine(day, time.min, tzinfo=timezone.utc)
        daily = []
        for channel_id, entry in catalogue.items():
            start_at = entry.get('start_at')
            if start_at in ('now', 'never'):
                continue
            if 'days' in entry and WEEKDAYS[day.weekday()] not in entry['days']:
                continue
            hour, minute = parse_military_time(start_at)
            daily.append({
                'id': channel_id,
                'start': midnight.replace(hour=hour, minute=minute),
                'title': entry.get('name', 'Unknown'),
                'desc': entry.get('details', ''),
                'prio': entry.get('prio', 0),
            })
        daily.sort(key=lambda programme: (programme['start'], -programme['prio'], str(programme['id'])))
        next_start = midnight + timedelta(days=1)
        previous_start = None
        boundary = next_start
        for programme in reversed(daily):
            if programme['start'] != previous_start:
                boundary = next_start
            programme['stop'] = boundary
            next_start = programme['start']
            previous_start = programme['start']
        programmes.extend(daily)
    return programmes


def programmes_for_guide(catalogue: dict, database: dict, now: datetime, count: int = 7) -> list[dict]:
    """Add nonrecurring runtime live entries to the UTC catalogue guide."""
    now = now.astimezone(timezone.utc)
    programmes = programmes_for_days(catalogue, now.date(), count)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for channel_id, entry in database.items():
        if entry.get('start_at') != 'now':
            continue
        days = entry.get('days', catalogue.get(channel_id, {}).get('days'))
        if days is not None and WEEKDAYS[now.weekday()] not in days:
            continue
        programmes.append({
            'id': channel_id,
            'start': now,
            'stop': min(now + timedelta(hours=3), midnight),
            'title': entry.get('name', 'Unknown'),
            'desc': entry.get('details', ''),
            'prio': entry.get('prio', 0),
            'live': True,
        })
    programmes.sort(key=lambda programme: (programme['start'], -programme['prio'], str(programme['id'])))
    return programmes
