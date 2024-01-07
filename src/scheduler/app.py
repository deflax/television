import os
import logging
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client
import time

app = Flask(__name__)
scheduler = BackgroundScheduler()

# Log handlers
logger_api = logging.getLogger('waitress')
logger_job = logging.getLogger('apscheduler')
log_level = os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper()
logger_api.setLevel(log_level)
logger_job.setLevel(log_level)

# Variables
CORE_SYNC_PERIOD = int(os.environ.get('CORE_SYNC_PERIOD', 30))
api_hostname = os.environ.get('CORE_API_HOSTNAME', 'stream.example.com')
api_username = os.environ.get('CORE_API_AUTH_USERNAME', 'admin')
api_password = os.environ.get('CORE_API_AUTH_PASSWORD', 'pass')

# Init
database = {}
prio = 0
head = {}

with open('/config/epg.json', 'r') as epg_json:
    epg = json.load(epg_json)
epg_json.close()

# Helper function to get process details
def get_core_process_details(client, process_id):
    try:
        return client.v3_process_get(id=process_id)
    except Exception as err:
        logger_job.error(f'Error getting process details for {process_id}: {err}')
        return None
    
# Helper function to process a running channel
def process_running_channel(database, scheduler, stream_id, stream_name, stream_description, stream_hls_url):
    if stream_id in database:
        # Skip learned channels
        return
    else:
        epg_result = find_event_entry(epg, stream_name)
        stream_start = epg_result.get('start_at')
        stream_prio = epg_result.get('prio', 0)
        if stream_start == "never":
            # Skip channels that are set to never start automatically
            return
        logger_job.info(f'{stream_id} ({stream_name}) has been registered to the database')      
        if stream_start == "now":
            logger_job.info("Stream should start now")
            scheduler.add_job(func=stream_exec, id=stream_id, args=(stream_id, stream_name, stream_prio, stream_hls_url))
        else:
            logger_job.info(f"Stream start hour is set to {stream_start}")
            scheduler.add_job(
                func=stream_exec, trigger='cron', hour=stream_start, jitter=60,
                id=stream_id, args=(stream_id, stream_name, stream_prio, stream_hls_url)
            )
        database.update({stream_id: {'name': stream_name, 'start_at': stream_start, 'meta': stream_description, 'src': stream_hls_url}})
        # Bootstrap the playhead if its still empty.
        if head == {}:
            fallback = fallback_search(database)
            scheduler.add_job(func=stream_exec, id="fallback", args=(fallback['stream_id'], fallback['stream_name'], 0, fallback['stream_hls_url']))

# Helper function to remove channel from the database
def remove_channel_from_database(database, scheduler, stream_id, stream_name, state):
    global prio
    global head
    if stream_id in database:
        logger_job.info(f'{stream_id} ({stream_name}) will be removed from the database. Reason: {state.exec}')
                # Handle the situation where we remove an stream that is currently playing
        if head['id'] == stream_id:
            logger_job.warning(f'{stream_id} was currently running.')
            fallback = fallback_search(database)
            prio = 0
            logger_job.info(f'Source priority is reset to 0')
            scheduler.add_job(func=stream_exec, id="fallback", args=(fallback['stream_id'], fallback['stream_name'], 0, fallback['stream_hls_url']))
        database.pop(stream_id)
        try:
            scheduler.remove_job(stream_id)
        except Exception as joberror:
            logger_job.error(joberror)            

# Helper function to search for a fallback stream
def fallback_search(database):
    logger_job.info('Searching for a fallback job.')
    current_hour = datetime.now().hour
    hour_set = []               
    for key, value in database.items():
        if value['start_at'] == "now" or value['start_at'] == "never":
            continue
        else:
            hour_set.append(value['start_at'])
        closest_hour = min(hour_set, key=lambda item: abs(int(item) - current_hour))
        for key, value in database.items():
            if value['start_at'] == str(closest_hour):
                fallback = { "stream_id": key,
                             "stream_name": value['name'],
                             "stream_hls_url": value['src']
                           }
                return fallback

# Helper function to find match a stream name with epg.json
def find_event_entry(epg, stream_name):
    for entry in epg:
        if "name" in entry and entry["name"] == stream_name:
            return {"start_at": entry.get("start_at"), "prio": entry.get("prio")}
    return None

# Helper function to update the head
def update_head(stream_id, stream_prio, stream_hls_url):
    global head
    head = { "id": stream_id,
             "prio": stream_prio,
             "head": stream_hls_url }
    logger_job.info(f'Head position is: {str(head)}')

# Tasks   
def stream_exec(stream_id, stream_name, stream_prio, stream_hls_url):
    global prio
    logger_job.info(f'Hello {stream_name}!')
    if stream_prio > prio:
        prio = stream_prio
        logger_job.info(f'Source priority is now set to: {prio}')
        update_head(stream_id, stream_prio, stream_hls_url)
    elif stream_prio == prio:
        update_head(stream_id, stream_prio, stream_hls_url)
    elif stream_prio < prio:
        logger_job.warning(f'Source with higher priority ({prio}) is blocking. Skipping head update!') 

def core_api_sync():
    global database
    global epg

    new_ids = []
    try:
        process_list = client.v3_process_get_list()
    except Exception as err:
        logger_job.error(f'Error getting process list: {err}')
        return True
    for process in process_list:
        try:
            get_process = get_core_process_details(client, process.id)
            if not get_process:
                continue
            stream_id = get_process.reference
            meta = get_process.metadata
            state = get_process.state
        except Exception as err:
            logger_job.error(f'Error processing {process.id}: {err}')
            continue
        if meta is None or meta['restreamer-ui'].get('meta') is None:
            # Skip processes without metadata or meta key
            continue
        new_ids.append(stream_id)
        stream_name = meta['restreamer-ui']['meta']['name']
        stream_description = meta['restreamer-ui']['meta']['description']
        stream_storage_type = meta['restreamer-ui']['control']['hls']['storage']
        stream_hls_url = f'https://{api_hostname}/{stream_storage_type}/{stream_id}.m3u8'

        if state.exec == "running":
            process_running_channel(database, scheduler, stream_id, stream_name, stream_description, stream_hls_url)
        else:
            remove_channel_from_database(database, scheduler, stream_id, stream_name, state)
            new_ids.remove(stream_id)

    # Cleanup orphaned references
    orphan_keys = [key for key in database if key not in new_ids]
    for orphan_key in orphan_keys:
        logger_job.info(f'Key {orphan_key} is an orphan. Removing.')
        database.pop(orphan_key)
        scheduler.remove_job(orphan_key)

# Login
# TODO fix logger_api
try:
    client = Client(base_url='https://' + api_hostname, username=api_username, password=api_password)
    logger_api.info('Logging in to Datarhei Core API ' + api_username + '@' + api_hostname)
    client.login()
except Exception as err:
    logger_api.error('Client login error')
    logger_api.error(err)
    
# Schedule datarhei core api sync
scheduler.add_job(func=core_api_sync, trigger="interval", seconds=CORE_SYNC_PERIOD, id="core_api_sync")

scheduler.start()

@app.route('/', methods=['GET'])
def root_query():
    global head
    return jsonify(head)

def create_app():
   return app
