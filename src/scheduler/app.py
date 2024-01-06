import os
import logging
import json
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client

app = Flask(__name__)
scheduler = BackgroundScheduler()

# Log handlers
logger_api = logging.getLogger('waitress')
logger_api.setLevel(os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper())

logger_job = logging.getLogger('apscheduler')
logger_job.setLevel(logging.DEBUG)

# Variables
CORE_SYNC_PERIOD = 30
api_hostname = os.environ.get('CORE_API_HOSTNAME')
api_port = os.environ.get('CORE_API_PORT')
api_username = os.environ.get('CORE_API_AUTH_USERNAME')
api_password=os.environ.get('CORE_API_AUTH_PASSWORD')

# Init
database = {}
prio = 0
head = {}
epg_json = open('/config/epg.json', 'r')
epg = json.load(epg_json)
logger_api.info(epg)
for i in epg:
    logger_api.info(i)
epg_json.close()

# Helper functions
def find_event_entry(events, target_name):
    for entry in events:
        if "name" in entry and entry["name"] == target_name:
            return {"start_at": entry.get("start_at"), "prio": entry.get("prio")}
    return None

# Tasks
def tick():
    print('Tick! The time is: %s' % datetime.now())
    
def stream_exec(stream_name, stream_prio, stream_hls_url):
    global head
    logger_job.info('Hello {}, your priority is'. format(stream_name, stream_prio))
    logger_job.info('HLS: ' + stream_hls_url)
    head = { "head": stream_hls_url }
   
def core_api_sync():
    global database
    global epg
    global prio
    new_ids = []
    try:
        process_list = client.v3_process_get_list()
    except Exception as err:
        logger_job.error('client.v3_process_get_list ' + err)
        return True
    for process in process_list:
        try:
            get_process = client.v3_process_get(id=process.id)
            stream_id = get_process.reference
            meta = get_process.metadata
            state = get_process.state
        except Exception as err:
            logger_job.error('client.v3_process_get ' + err)
            continue
        if meta is None:
            # Skip processes without metadata
            continue
        else:
            if meta['restreamer-ui'].get('meta') is None:
                # Skip processes without meta key
                #logger_job.warn('{} does not have a meta key'.format(stream_id))
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
                    # Skip learned channels
                    continue
                else:
                    logger_job.info('{} ({}) has been registered to the database'.format(stream_id, stream_name))
                    epg_result = find_event_entry(epg, stream_name)
                    logger_job.info(epg_result)
                    #stream_prio = epg_result['prio']
                    stream_prio = 0
                    try:
                        stream_start_hour = epg_result['start_at']
                        logger_job.info("Stream start hour is set to " + stream_start_hour)                       
                        scheduler.add_job(func=stream_exec, trigger='cron', hour=stream_start_hour, jitter=60, id=stream_id, args=(stream_name, stream_prio, stream_hls_url))
                    except KeyError:
                        logger_job.info("Stream should start now")
                        scheduler.add_job(func=stream_exec, id=stream_id, args=(stream_name, stream_prio, stream_hls_url))
                    database.update(payload)
            else:
                # Remove from the database if the state is changed
                if stream_id in database:
                    logger_job.info('{} ({}) has been removed from the database. Reason: {}'.format(stream_id, stream_name, state.exec))
                    database.pop(stream_id)
                    scheduler.remove_job(stream_id)
                    new_ids.remove(stream_id)
    # Cleanup orphaned references
    orphan_keys = []
    for key in database:
        if key in new_ids:
            continue
        else:
            logger_job.info('Key {} is an orphan. Removing.'.format(key))
            orphan_keys.append(key)
    for orphan_key in orphan_keys:
        database.pop(orphan_key)
        scheduler.remove_job(stream_id)

def show_database():
    global database
    logger_job.info('Scheduler DB: ' + str(database))
    
def show_scheduled_tasks():
    logger_job.info('Scheduler tasks:' + str(scheduler.get_jobs()))
    logger_job.info('Scheduler tasks:' + str(scheduler.print_jobs()))

# Login
try:
    client = Client(base_url='https://' + api_hostname, username=api_username, password=api_password)
    logger_job.info('Logging in to Datarhei Core API ' + api_username + '@' + api_hostname)
    client.login()
except Exception as err:
    logger_job.error('client login error')
    logger_job.error(err)


# Schedule tick
scheduler.add_job(func=tick, trigger="interval", minutes=180)
    
# Schedule datarhei core api sync
#core_api_sync()
scheduler.add_job(func=core_api_sync, trigger="interval", seconds=CORE_SYNC_PERIOD, id="core_api_sync")

# Schedule show db/tasks
scheduler.add_job(func=show_database, trigger="interval", seconds=60, id="show_database")
scheduler.add_job(func=show_scheduled_tasks, trigger="interval", seconds=60, id="show_scheduled_tasks")

scheduler.start()

fallback = { "head": "https://stream.deflax.net/memfs/938a36f8-02ff-4452-a7e5-3b6a9a07cdfa.m3u8" }
head = fallback

@app.route('/', methods=['GET'])
def root_query():
    global head
    return jsonify(head)

def create_app():
   return app
