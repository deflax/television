# pyright: reportMissingModuleSource=false

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Protocol

import re
import socket
import ssl
from urllib.parse import SplitResult, urlsplit

import requests


class _IcyRawResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class _SocketStream(Protocol):
    def settimeout(self, value: float | None, /) -> None: ...

    def sendall(self, data: bytes, flags: int = 0, /) -> None: ...

    def recv(self, bufsize: int, flags: int = 0, /) -> bytes: ...

    def close(self) -> None: ...


class _IcyResponse(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def raw(self) -> _IcyRawResponse: ...


_STREAM_TITLE_PATTERN = re.compile(r"StreamTitle='(.*?)';")
_ICY_HEADERS = {'Icy-MetaData': '1'}
_HEADER_TERMINATOR = b'\r\n\r\n'
_MAX_HEADER_BYTES = 65536
_MAX_METADATA_BLOCKS = 5


@dataclass(frozen=True, slots=True)
class IcyMetadataResult:
    title: str | None
    reason: str


def read_icy_stream_title(source_url: str, timeout: float) -> str | None:
    return read_icy_metadata_result(source_url, timeout).title


def read_icy_metadata_result(source_url: str, timeout: float) -> IcyMetadataResult:
    parsed_url = urlsplit(source_url)
    if parsed_url.scheme not in {'http', 'https'}:
        return IcyMetadataResult(title=None, reason=f'unsupported-scheme:{parsed_url.scheme}')

    socket_result = _read_icy_stream_title_from_socket(parsed_url, timeout)
    if socket_result.title:
        return socket_result

    try:
        response = requests.get(source_url, headers=_ICY_HEADERS, stream=True, timeout=timeout)
    except requests.RequestException:
        return socket_result

    response_result = _read_stream_title_from_response(response)
    if response_result.title:
        return response_result
    return IcyMetadataResult(title=None, reason=f'socket={socket_result.reason};requests={response_result.reason}')


def _read_stream_title_from_response(response: _IcyResponse) -> IcyMetadataResult:
    try:
        metaint = int(response.headers.get('icy-metaint', ''))
    except (TypeError, ValueError):
        return IcyMetadataResult(title=None, reason='missing-or-invalid-icy-metaint')

    if metaint <= 0:
        return IcyMetadataResult(title=None, reason=f'invalid-icy-metaint:{metaint}')

    last_reason = 'no-metadata-blocks-read'
    for _ in range(_MAX_METADATA_BLOCKS):
        title, reason = _read_next_stream_title(response.raw, metaint)
        if title:
            return IcyMetadataResult(title=title, reason='ok')
        last_reason = reason
    return IcyMetadataResult(
        title=None,
        reason=f'no-title-after-{_MAX_METADATA_BLOCKS}-blocks:last={last_reason}',
    )


def _read_next_stream_title(raw: _IcyRawResponse, metaint: int) -> tuple[str | None, str]:
    audio_bytes = _read_exact(raw, metaint)
    if len(audio_bytes) != metaint:
        return None, f'short-audio-read:{len(audio_bytes)}/{metaint}'

    metadata_length_bytes = _read_exact(raw, 1)
    if len(metadata_length_bytes) != 1:
        return None, 'short-metadata-length-read'

    metadata_length = metadata_length_bytes[0] * 16
    if metadata_length == 0:
        return None, 'empty-metadata-block'

    metadata_bytes = _read_exact(raw, metadata_length)
    if len(metadata_bytes) != metadata_length:
        return None, f'short-metadata-read:{len(metadata_bytes)}/{metadata_length}'

    try:
        metadata_text = metadata_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'metadata-decode-error'

    match = _STREAM_TITLE_PATTERN.search(metadata_text)
    if match is None:
        return None, 'stream-title-missing'

    title = match.group(1)
    if not title:
        return None, 'stream-title-empty'

    return title, 'ok'


def _read_exact(raw: _IcyRawResponse, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = raw.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


class _BufferedSocketRaw:
    def __init__(self, stream: _SocketStream, buffered: bytes):
        self.stream: _SocketStream = stream
        self.buffered: bytes = buffered

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.buffered)
        if self.buffered:
            result = self.buffered[:size]
            self.buffered = self.buffered[size:]
            if len(result) == size:
                return result
            return result + self.stream.recv(size - len(result))
        return self.stream.recv(size)


class _SocketIcyResponse:
    def __init__(self, headers: Mapping[str, str], raw: _IcyRawResponse):
        self._headers: Mapping[str, str] = headers
        self._raw: _IcyRawResponse = raw

    @property
    def headers(self) -> Mapping[str, str]:
        return self._headers

    @property
    def raw(self) -> _IcyRawResponse:
        return self._raw


def _read_icy_stream_title_from_socket(parsed_url: SplitResult, timeout: float) -> IcyMetadataResult:
    host = parsed_url.hostname
    if host is None:
        return IcyMetadataResult(title=None, reason='missing-host')

    stream = _open_socket_stream(parsed_url, host, timeout)
    if stream is None:
        return IcyMetadataResult(title=None, reason='socket-open-failed')

    try:
        stream.sendall(_build_socket_request(parsed_url, host))
        header_bytes, buffered = _read_socket_headers(stream)
        if not header_bytes:
            return IcyMetadataResult(title=None, reason='socket-header-missing')
        headers = _parse_socket_headers(header_bytes)
        return _read_stream_title_from_response(_SocketIcyResponse(headers, _BufferedSocketRaw(stream, buffered)))
    finally:
        stream.close()


def _open_socket_stream(parsed_url: SplitResult, host: str, timeout: float) -> _SocketStream | None:
    port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
    try:
        stream = socket.create_connection((host, port), timeout=timeout)
        stream.settimeout(timeout)
        if parsed_url.scheme == 'https':
            return ssl.create_default_context().wrap_socket(stream, server_hostname=host)
        return stream
    except OSError:
        return None


def _build_socket_request(parsed_url: SplitResult, host: str) -> bytes:
    path = parsed_url.path or '/'
    if parsed_url.query:
        path = f'{path}?{parsed_url.query}'
    return (
        f'GET {path} HTTP/1.0\r\n'
        f'Host: {host}\r\n'
        'User-Agent: television-metadata/1\r\n'
        'Icy-MetaData: 1\r\n'
        '\r\n'
    ).encode('ascii')


def _read_socket_headers(stream: _SocketStream) -> tuple[bytes, bytes]:
    data = b''
    while _HEADER_TERMINATOR not in data and len(data) < _MAX_HEADER_BYTES:
        chunk = stream.recv(4096)
        if not chunk:
            break
        data += chunk
    split_at = data.find(_HEADER_TERMINATOR)
    if split_at < 0:
        return b'', b''
    return data[:split_at], data[split_at + len(_HEADER_TERMINATOR):]


def _parse_socket_headers(header_bytes: bytes) -> Mapping[str, str]:
    headers: dict[str, str] = {}
    text = header_bytes.decode('iso-8859-1', errors='replace')
    for line in text.split('\r\n')[1:]:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        headers[key.strip().lower()] = value.strip()
    return headers
