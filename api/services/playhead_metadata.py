from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, TypeAlias
from urllib.parse import urlparse

from .icy_metadata import read_icy_metadata_result


JsonValue: TypeAlias = str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue']
ProcessDetail: TypeAlias = dict[str, JsonValue]


class CoreProcessClient(Protocol):
    def v3_process_get_list(self) -> list[ProcessDetail]: ...

    def v3_process_get(self, id: str) -> ProcessDetail: ...

    def v3_process_get_config(self, id: str) -> ProcessDetail: ...


class PlayheadMetadataPoller:
    def __init__(self, client: CoreProcessClient, logger: logging.Logger):
        self.client: CoreProcessClient = client
        self.logger: logging.Logger = logger

    def poll(self, playhead: ProcessDetail, timeout: float) -> ProcessDetail:
        current_id = playhead.get('id')
        if not isinstance(current_id, str) or not current_id:
            return playhead

        channel_name = _channel_name(playhead)
        source_url = self._current_source_url(current_id)
        if not source_url:
            self.logger.info(f'No metadata source URL found for {channel_name}')
            return _without_metadata(playhead)

        self.logger.info(f'Polling ICY metadata for {channel_name} from {source_url}')
        metadata_result = read_icy_metadata_result(source_url, timeout)
        next_playhead = dict(playhead)
        if metadata_result.title:
            next_playhead['metadata'] = {'stream_title': metadata_result.title}
            self.logger.info(f'Updated ICY metadata for {channel_name}: {metadata_result.title}')
        else:
            _ = next_playhead.pop('metadata', None)
            self.logger.info(f'No ICY metadata title found for {channel_name}: {metadata_result.reason}')
        return next_playhead

    def _current_source_url(self, current_id: str) -> str | None:
        ingest_config = self._get_process_config(_restreamer_ingest_process_id(current_id))
        if ingest_config:
            ingest_source_url = _source_url_from_process_config(ingest_config)
            if ingest_source_url:
                return ingest_source_url

        try:
            process_list = self.client.v3_process_get_list()
        except Exception as e:
            self.logger.error(f'Error getting process list for metadata polling: {e}')
            return None

        for process in sorted(process_list, key=_process_priority(current_id)):
            process_id = process.get('id')
            if not isinstance(process_id, str):
                continue
            details = self._get_process_details(process_id)
            if not details or details.get('reference') != current_id:
                continue
            source_url = _source_url_from_process_details(details)
            if source_url:
                return source_url
        return None

    def _get_process_config(self, process_id: str) -> ProcessDetail | None:
        try:
            return self.client.v3_process_get_config(id=process_id)
        except Exception as e:
            self.logger.debug(f'Error getting process config for metadata polling {process_id}: {e}')
            return None

    def _get_process_details(self, process_id: str) -> ProcessDetail | None:
        try:
            return self.client.v3_process_get(id=process_id)
        except Exception as e:
            self.logger.error(f'Error getting process details for metadata polling {process_id}: {e}')
            return None


def _source_url_from_process_details(details: ProcessDetail) -> str | None:
    extractors: tuple[Callable[[ProcessDetail], tuple[str, ...]], ...] = (
        _source_urls_from_config_input,
        _source_urls_from_input,
        _source_urls_from_inputs,
    )
    for extract in extractors:
        for source_url in extract(details):
            if _is_external_icy_source(source_url):
                return source_url
    return None


def _source_url_from_process_config(config: ProcessDetail) -> str | None:
    for source_url in _source_urls_from_input_list(config.get('input')):
        if _is_external_icy_source(source_url):
            return source_url
    return None


def _source_urls_from_config_input(details: ProcessDetail) -> tuple[str, ...]:
    config = details.get('config')
    if not isinstance(config, dict):
        return ()
    return _source_urls_from_input_list(config.get('input'))


def _source_urls_from_input(details: ProcessDetail) -> tuple[str, ...]:
    input_config = details.get('input')
    if not isinstance(input_config, dict):
        return ()
    return _addresses_from_mappings((input_config,))


def _source_urls_from_inputs(details: ProcessDetail) -> tuple[str, ...]:
    return _source_urls_from_input_list(details.get('inputs'))


def _source_urls_from_input_list(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return _addresses_from_mappings(tuple(item for item in value if isinstance(item, dict)))


def _addresses_from_mappings(values: tuple[ProcessDetail, ...]) -> tuple[str, ...]:
    addresses: list[str] = []
    for value in values:
        address = _address_from_mapping(value)
        if address:
            addresses.append(address)
    return tuple(addresses)


def _address_from_mapping(value: ProcessDetail) -> str | None:
    address = value.get('address')
    if isinstance(address, str) and address:
        return address
    return None


def _is_external_icy_source(source_url: str) -> bool:
    if source_url.startswith(('{', '#', '/memfs')):
        return False
    parsed = urlparse(source_url)
    if parsed.scheme not in {'http', 'https'}:
        return False
    if parsed.hostname in {'localhost', '127.0.0.1', '::1'}:
        return False
    return not parsed.path.startswith('/memfs')


def _restreamer_ingest_process_id(channel_id: str) -> str:
    return f'restreamer-ui:ingest:{channel_id}'


def _process_priority(current_id: str) -> Callable[[ProcessDetail], int]:
    ingest_process_id = _restreamer_ingest_process_id(current_id)

    def priority(process: ProcessDetail) -> int:
        return 0 if process.get('id') == ingest_process_id else 1

    return priority


def _channel_name(playhead: ProcessDetail) -> str:
    name = playhead.get('name')
    if isinstance(name, str) and name:
        return name
    return 'current playhead'


def _without_metadata(playhead: ProcessDetail) -> ProcessDetail:
    next_playhead = dict(playhead)
    _ = next_playhead.pop('metadata', None)
    return next_playhead
