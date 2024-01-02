import os
import logging
import asyncio
from datetime import datetime
from schedule_manager import ScheduleManager
from flask import Flask, render_template, jsonify, request
from core_client import Client

app = Flask(__name__)
manager = ScheduleManager()
logger = logging.getLogger('waitress')
logger.setLevel(os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper())
database = {}

# Environment
api_hostname = os.environ.get('CORE_API_HOSTNAME')
api_port = os.environ.get('CORE_API_PORT')
api_username = os.environ.get('CORE_API_AUTH_USERNAME')
api_password=os.environ.get('CORE_API_AUTH_PASSWORD')

iway = {
    "head": "https://stream.deflax.net/memfs/ac2172fa-d8d5-4487-8bc6-5347dcf7c0dc.m3u8"
}

cam = {
    "head": "https://stream.deflax.net/memfs/37890510-5ff7-4387-866f-516468cea43f.m3u8"
}

jt = {
    "head": "https://stream.deflax.net/memfs/6e5b4949-910d-4ec9-8ed8-1a3d8bb5138e.m3u8"
}

obs = {
    "head": "https://stream.deflax.net/memfs/9502315a-bb95-4e3e-8c24-8661d6dd2fe8.m3u8"
}

# Datarhei Core API integration
SYNC_PERIOD = 30
try:
    client = Client(base_url='https://' + api_hostname, username=api_username, password=api_password)
    logger.info('Logging in to Datarhei Core API ' + api_username + '@' + api_hostname)
    client.login()
except Exception as err:
    logger.error(err)

def core_api_sync():
    new_ids = []
    try:
       process_list = client.v3_process_get_list()
    except Exception as err:
        logger.error('process_get_list error')
        return True
    for process in process_list:
        get_process = client.v3_process_get(id=process.id)
        stream_id = get_process.reference
        meta = get_process.metadata
        state = get_process.state
        if meta is None:
            # Skip processes without metadata
            continue
        else:
            if meta['restreamer-ui'].get('meta') is None:
                # Skip processes without meta key
                #logger.warn('{} does not have a meta key'.format(stream_id))
                continue
            new_ids.append(stream_id)
            stream_name = meta['restreamer-ui']['meta']['name']
            stream_description = meta['restreamer-ui']['meta']['description']
            stream_storage_type = meta['restreamer-ui']['control']['hls']['storage']
            stream_hls_url = 'https://{}/{}/{}.m3u8'.format(api_hostname, stream_storage_type, stream_id)

            payload = { stream_id: { 'name': stream_name, 'meta': stream_description, 'src': stream_hls_url } }

            if state.exec == "running":
                # Register a running channel to the database
                if stream_id in database:
                    # Skip overwriting channel
                    continue
                else:
                    logger.info('{} ({}) has been registered to the database'.format(stream_id, stream_name))
                    database.update(payload)
            else:
                # Remove from the database if the state is changed
                if stream_id in database:
                    logger.info('{} ({}) has been removed from the database. Reason: {}'.format(stream_id, stream_name, state.exec))
                    database.pop(stream_id)
                    new_ids.remove(stream_id)

    # Cleanup orphaned references
    marked_keys = []
    for key in database:
        if key in new_ids:
            continue
        else:
            logger.info('Key {} is an orphan. Removing.'.format(key))
            marked_keys.append(key)

    for marked_key in marked_keys:
        database.pop(marked_key)

def analyze_db():
    #logger.info(database)
    return True

# Schedule a periodic task: sync datarhei core api
manager.register_task(name="core_api_sync", job=core_api_sync).period(SYNC_PERIOD).start()

# Schedule a periodic task: read the memory state
manager.register_task(name="read_database", job=analyze_db).period(35).start()

@app.route('/', methods=['GET'])
def root_query():
    playhead = jt
    return jsonify(playhead)

def create_app():
   return app
