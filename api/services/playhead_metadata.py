from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol, TypeAlias

from .icy_metadata import read_icy_stream_title


JsonValue: TypeAlias = str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue']
ProcessDetail: TypeAlias = dict[str, JsonValue]


class CoreProcessClient(Protocol):
    def v3_process_get_list(self) -> list[ProcessDetail]: ...

    def v3_process_get(self, id: str) -> ProcessDetail: ...


class PlayheadMetadataPoller:
    def __init__(self, client: CoreProcessClient, logger: logging.Logger):
        self.client: CoreProcessClient = client
        self.logger: logging.Logger = logger

    def poll(self, playhead: ProcessDetail, timeout: float) -> ProcessDetail:
        current_id = playhead.get('id')
        if not isinstance(current_id, str) or not current_id:
            return playhead

        source_url = self._current_source_url(current_id)
        title = read_icy_stream_title(source_url, timeout) if source_url else None
        next_playhead = dict(playhead)
        if title:
            next_playhead['metadata'] = {'stream_title': title}
        else:
            _ = next_playhead.pop('metadata', None)
        return next_playhead

    def _current_source_url(self, current_id: str) -> str | None:
        try:
            process_list = self.client.v3_process_get_list()
        except Exception as e:
            self.logger.error(f'Error getting process list for metadata polling: {e}')
            return None

        for process in process_list:
            process_id = process.get('id')
            if not isinstance(process_id, str):
                continue
            details = self._get_process_details(process_id)
            if not details or details.get('reference') != current_id:
                continue
            return _source_url_from_process_details(details)
        return None

    def _get_process_details(self, process_id: str) -> ProcessDetail | None:
        try:
            return self.client.v3_process_get(id=process_id)
        except Exception as e:
            self.logger.error(f'Error getting process details for metadata polling {process_id}: {e}')
            return None


def _source_url_from_process_details(details: ProcessDetail) -> str | None:
    extractors: tuple[Callable[[ProcessDetail], str | None], ...] = (
        _source_url_from_config_input,
        _source_url_from_input,
        _source_url_from_inputs,
    )
    for extract in extractors:
        source_url = extract(details)
        if source_url:
            return source_url
    return None


def _source_url_from_config_input(details: ProcessDetail) -> str | None:
    config = details.get('config')
    if not isinstance(config, dict):
        return None
    input_list = config.get('input')
    if not isinstance(input_list, list) or not input_list:
        return None
    first_input = input_list[0]
    if not isinstance(first_input, dict):
        return None
    return _address_from_mapping(first_input)


def _source_url_from_input(details: ProcessDetail) -> str | None:
    input_config = details.get('input')
    if not isinstance(input_config, dict):
        return None
    return _address_from_mapping(input_config)


def _source_url_from_inputs(details: ProcessDetail) -> str | None:
    inputs = details.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        return None
    first_input = inputs[0]
    if not isinstance(first_input, dict):
        return None
    return _address_from_mapping(first_input)


def _address_from_mapping(value: ProcessDetail) -> str | None:
    address = value.get('address')
    if isinstance(address, str) and address:
        return address
    return None
