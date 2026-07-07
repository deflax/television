# pyright: reportMissingModuleSource=false

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import re
from urllib.parse import urlsplit

import requests


class _IcyRawResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class _IcyResponse(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def raw(self) -> _IcyRawResponse: ...


_STREAM_TITLE_PATTERN = re.compile(r"StreamTitle='(.*?)';")
_ICY_HEADERS = {'Icy-MetaData': '1'}


def read_icy_stream_title(source_url: str, timeout: float) -> str | None:
    if urlsplit(source_url).scheme not in {'http', 'https'}:
        return None

    try:
        response = requests.get(source_url, headers=_ICY_HEADERS, stream=True, timeout=timeout)
    except requests.RequestException:
        return None

    return _read_stream_title_from_response(response)


def _read_stream_title_from_response(response: _IcyResponse) -> str | None:
    try:
        metaint = int(response.headers.get('icy-metaint', ''))
    except (TypeError, ValueError):
        return None

    if metaint <= 0:
        return None

    audio_bytes = response.raw.read(metaint)
    if len(audio_bytes) != metaint:
        return None

    metadata_length_bytes = response.raw.read(1)
    if len(metadata_length_bytes) != 1:
        return None

    metadata_length = metadata_length_bytes[0] * 16
    if metadata_length == 0:
        return None

    metadata_bytes = response.raw.read(metadata_length)
    if len(metadata_bytes) != metadata_length:
        return None

    try:
        metadata_text = metadata_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return None

    match = _STREAM_TITLE_PATTERN.search(metadata_text)
    if match is None:
        return None

    title = match.group(1)
    if not title:
        return None

    return title
