import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from quart import request

from web.helpers import get_client_address


def register_playlist_routes(app, stream_manager, loggers) -> None:
    """Register playlist and EPG generation routes."""

    @app.route('/live.m3u8', methods=['GET'])
    async def live_m3u8_route():
        """Serve dynamically generated live.m3u8 playlist file."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] live.m3u8')

        host = request.headers.get('Host') or request.host
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        domain = host.split(':')[0] if ':' in host else host

        epg_url = f'{scheme}://{host}/epg.xml'
        channel_id = domain.lower()
        playlist_content = f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}" url-tvg-refresh="1"\n'
        playlist_content += (
            f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{domain}" '
            f'tvg-logo="{scheme}://{host}/static/images/logo.png" group-title="Relax",{domain}\n'
        )
        playlist_content += f'{scheme}://{host}/live/stream.m3u8\n'

        response = await app.make_response(playlist_content)
        response.headers['Content-Type'] = 'application/vnd.apple.mpegurl'
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.route('/epg.xml', methods=['GET'])
    async def epg_xml_route():
        """Serve dynamically generated XMLTV EPG from the stream database."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] epg.xml')

        host = request.headers.get('Host') or request.host
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        domain = host.split(':')[0] if ':' in host else host
        channel_id = domain.lower()

        tv = ET.Element('tv', attrib={
            'generator-info-name': 'television-epg',
            'generator-info-url': f'{scheme}://{host}',
        })

        channel = ET.SubElement(tv, 'channel', id=channel_id)
        ET.SubElement(channel, 'display-name').text = domain
        ET.SubElement(channel, 'icon', src=f'{scheme}://{host}/static/images/logo.png')

        if stream_manager is not None and stream_manager.database:
            now = datetime.now(timezone.utc)
            live_programmes = []
            scheduled_programmes = []

            for _, entry in stream_manager.database.items():
                start_at = entry.get('start_at', '')
                name = entry.get('name', 'Unknown')
                details = entry.get('details', '')

                if start_at == 'never':
                    continue

                if start_at == 'now':
                    live_programmes.append({
                        'start': now,
                        'stop': now + timedelta(hours=3),
                        'title': name,
                        'desc': details,
                    })
                else:
                    time_str = str(start_at).strip()
                    if len(time_str) <= 2:
                        hour, minute = int(time_str), 0
                    else:
                        hour, minute = int(time_str[:-2]), int(time_str[-2:])
                    prog_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if prog_start < now - timedelta(hours=1):
                        prog_start += timedelta(days=1)
                    scheduled_programmes.append({
                        'start': prog_start,
                        'title': name,
                        'desc': details,
                    })

            scheduled_programmes.sort(key=lambda prog: prog['start'])

            for idx, prog in enumerate(scheduled_programmes):
                if idx + 1 < len(scheduled_programmes):
                    prog['stop'] = scheduled_programmes[idx + 1]['start']
                elif len(scheduled_programmes) > 1:
                    prog['stop'] = scheduled_programmes[0]['start'] + timedelta(days=1)
                else:
                    prog['stop'] = prog['start'] + timedelta(hours=24)

            programmes = live_programmes + scheduled_programmes

            for prog in programmes:
                fmt = '%Y%m%d%H%M%S +0000'
                prog_el = ET.SubElement(
                    tv,
                    'programme',
                    attrib={
                        'start': prog['start'].strftime(fmt),
                        'stop': prog['stop'].strftime(fmt),
                        'channel': channel_id,
                    },
                )
                ET.SubElement(prog_el, 'title', lang='en').text = prog['title']
                if prog.get('desc'):
                    ET.SubElement(prog_el, 'desc', lang='en').text = prog['desc']

        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_doctype = '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
        ET.indent(tv, space='  ')
        xml_body = ET.tostring(tv, encoding='unicode', xml_declaration=False)
        xml_content = xml_declaration + xml_doctype + xml_body + '\n'

        response = await app.make_response(xml_content)
        response.headers['Content-Type'] = 'application/xml; charset=utf-8'
        response.headers['Cache-Control'] = 'no-cache'
        return response
