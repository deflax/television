import os
import logging
import time
from datetime import datetime
import threading
import json
from flask import Flask, render_template, jsonify, request
from flask_apscheduler import APScheduler
from core_client import Client

logger = logging.getLogger('waitress')
logger.setLevel(os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper())

app = Flask(__name__)

scheduler = APScheduler()
scheduler.api_enabled = False
scheduler.init_app(app)
scheduler.start()

database = {}
prio = 0
head = {}

epg_json = open('/config/epg.json', 'r')
epg = json.load(epg_json)
logger.info(epg)

for i in epg:
    logger.info(i)

epg_json.close()

# Environment
api_hostname = os.environ.get('CORE_API_HOSTNAME')
api_port = os.environ.get('CORE_API_PORT')
api_username = os.environ.get('CORE_API_AUTH_USERNAME')
api_password=os.environ.get('CORE_API_AUTH_PASSWORD')

# Helper functions
def find_event_entry(events, target_name):
    for entry in events:
        if "name" in entry and entry["name"] == target_name:
            return {"start_at": entry.get("start_at"), "prio": entry.get("prio")}
    return None
    
def stream_exec(stream_name, stream_prio, stream_hls_url):
    global head
    logger.info('Hello {}, your priority is'. format(stream_name, stream_prio))
    logger.info('HLS: ' + stream_hls_url)
    head = { "head": stream_hls_url }

# Datarhei Core API integration
SYNC_PERIOD = 30
try:
    client = Client(base_url='https://' + api_hostname, username=api_username, password=api_password)
    logger.info('Logging in to Datarhei Core API ' + api_username + '@' + api_hostname)
    client.login()
except Exception as err:
    logger.error('client login error')
    logger.error(err)
    
def core_api_sync():
    global database
    global epg
    global prio
    new_ids = []
    try:
        process_list = client.v3_process_get_list()
    except Exception as err:
        logger.error('client.v3_process_get_list ' + err)
        return True
    for process in process_list:
        try:
            get_process = client.v3_process_get(id=process.id)
            stream_id = get_process.reference
            meta = get_process.metadata
            state = get_process.state
        except Exception as err:
            logger.error('client.v3_process_get ' + err)
            continue
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
                    # Skip learned channels
                    continue
                else:
                    logger.info('{} ({}) has been registered to the database'.format(stream_id, stream_name))
                    epg_result = find_event_entry(epg, stream_name)
                    logger.info(epg_result)
                    #stream_prio = epg_result['prio']
                    stream_prio = 0
                    try:
                        #stream_start_time = epg_result['start_at']
                        stream_start_time = "08:00"
                        logger.info("Start time is set to " + stream_start_time)
                        schedule.every().day.at(stream_start_time).do(stream_exec, stream_name, stream_prio, stream_hls_url).tag(stream_id)
                        scheduler.add_job(myfunc, 'interval', minutes=2, id='my_job_id')
                    except KeyError:
                        logger.info("Stream should start a.s.a.p")
                        schedule.every().minute.do(stream_exec, stream_name, stream_prio, stream_hls_url).tag(stream_id)
                    database.update(payload)
            else:
                # Remove from the database if the state is changed
                if stream_id in database:
                    logger.info('{} ({}) has been removed from the database. Reason: {}'.format(stream_id, stream_name, state.exec))
                    database.pop(stream_id)
                    schedule.clear(stream_id)
                    new_ids.remove(stream_id)
    # Cleanup orphaned references
    orphan_keys = []
    for key in database:
        if key in new_ids:
            continue
        else:
            logger.info('Key {} is an orphan. Removing.'.format(key))
            orphan_keys.append(key)
    for orphan_key in orphan_keys:
        database.pop(orphan_key)
        schedule.clear(stream_id)

# Debug Functions
def show_database():
    global database
    logger.info('Scheduler DB: ' + str(database))
    
def show_scheduled_tasks():
    logger.info('Scheduler tasks:' + str(schedule.get_jobs()))

# Schedule datarhei core api sync
core_api_sync()
scheduler.add_job(func=core_api_sync, trigger="interval", seconds=SYNC_PERIOD, id="core_api_sync", name="core_api_sync", replace_existing=True)

# Schedule show db/tasks
scheduler.add_job(func=core_api_sync, trigger="interval", seconds=60, id="core_api_sync", name="core_api_sync", replace_existing=True)
scheduler.add_job(func=core_api_sync, trigger="interval", seconds=60, id="core_api_sync", name="core_api_sync", replace_existing=True)

schedule.every().minute.do(show_database)
schedule.every().minute.do(show_scheduled_tasks)

fallback = { "head": "https://stream.deflax.net/memfs/938a36f8-02ff-4452-a7e5-3b6a9a07cdfa.m3u8" }
head = fallback

@app.route('/', methods=['GET'])
def root_query():
    global head
    return jsonify(head)

def create_app():
   return app
