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

print(epg)
logger_api.info(epg)

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
            scheduler.add_job(func=stream_exec, id=stream_id, args=(stream_name, stream_prio, stream_hls_url))
        else:
            logger_job.info(f"Stream start hour is set to {stream_start}")
            scheduler.add_job(
                func=stream_exec, trigger='cron', hour=stream_start, jitter=60,
                id=stream_id, args=(stream_name, stream_prio, stream_hls_url)
            )            
        database.update({stream_id: {'name': stream_name, 'meta': stream_description, 'src': stream_hls_url}})

# Helper function to remove channel from the database
def remove_channel_from_database(database, scheduler, stream_id, stream_name, state):
    if stream_id in database:
        logger_job.info(f'{stream_id} ({stream_name}) has been removed from the database. Reason: {state.exec}')
        database.pop(stream_id)
        try:
            scheduler.remove_job(stream_id)
        except scheduler.jobstores.base.JobLookupError as je:
            logger_job.error(je)

# Helper function to find match a stream name with epg.json
def find_event_entry(events, target_name):
    for entry in events:
        if "name" in entry and entry["name"] == target_name:
            return {"start_at": entry.get("start_at"), "prio": entry.get("prio")}
    return None

# Tasks   
def stream_exec(stream_name, stream_prio, stream_hls_url):
    global head
    logger_job.info('Hello {}, your priority is: {}'. format(stream_name, stream_prio))
    head = { "head": stream_hls_url }
    logger_job.info('head position is: ' + str(head))
   
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

def show_database():
    logger_job.info('Scheduler DB: ' + str(database))
    
def show_scheduled_tasks():
    logger_job.info('Scheduler tasks:' + str(scheduler.get_jobs()))

# Login
try:
    client = Client(base_url='https://' + api_hostname, username=api_username, password=api_password)
    logger_api.info('Logging in to Datarhei Core API ' + api_username + '@' + api_hostname)
    client.login()
except Exception as err:
    logger_api.error('client login error')
    logger_api.error(err)
    
# Schedule datarhei core api sync
scheduler.add_job(func=core_api_sync, trigger="interval", seconds=CORE_SYNC_PERIOD, id="core_api_sync")

# Schedule show db/tasks
scheduler.add_job(func=show_database, trigger="interval", minutes=60, id="show_database")
scheduler.add_job(func=show_scheduled_tasks, trigger="interval", minutes=60, id="show_scheduled_tasks")

scheduler.start()

@app.route('/', methods=['GET'])
def root_query():
    global head
    return jsonify(head)

def create_app():
   return app
