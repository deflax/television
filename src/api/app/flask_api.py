import time
import sys
import os
import ast
import subprocess
import logging
import json
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, abort
from flask.helpers import send_file, send_from_directory
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client

app = Flask(__name__)
app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME')
scheduler = BackgroundScheduler()

# Variables
log_level = os.environ.get('FLASKAPI_LOG_LEVEL', 'INFO').upper()
vod_token = os.environ.get('FLASKAPI_VOD_TOKEN')
core_hostname = os.environ.get('CORE_API_HOSTNAME', 'stream.example.com')
core_username = os.environ.get('CORE_API_AUTH_USERNAME', 'admin')
core_password = os.environ.get('CORE_API_AUTH_PASSWORD', 'pass')
core_sync_period = int(os.environ.get('CORE_SYNC_PERIOD', 15))

rec_path = "/recordings"
enable_delay = 24

# Log handlers
logger_api = logging.getLogger('waitress')
logger_job = logging.getLogger('apscheduler')
logger_content = logging.getLogger('content')

logger_api.setLevel(log_level)
logger_job.setLevel(log_level)
logger_content = logging.getLogger('content')

# Init
database = {}
playhead = {}
prio = 0

# Helper function to get process details
def get_core_process_details(client, process_id):
    try:
        return client.v3_process_get(id=process_id)
    except Exception as e:
        logger_job.error(f'Error getting process details for {process_id}: {e}')
        return None
    
# Process a running channel
def process_running_channel(database, scheduler, stream_id, stream_name, stream_description, stream_hls_url):
    if stream_id in database:
        # Skip already learned channels
        return
    else:
        try:
            # Get the channel settings from the stream description
            api_settings = ast.literal_eval(stream_description)
            stream_start = api_settings.get('start_at')
            stream_prio = api_settings.get('prio', 0)
        except Exception as e:
            # Skip channels without readable meta
            return
        logger_job.warning(f'{stream_id} ({stream_name}) found. {api_settings} ')

        # Check whether we have stream details
        stream_details = api_settings.get('details')
        if stream_details is None:
            stream_details = ""
        else:
            logger_job.warning(f'Details found: {stream_details}')

        if stream_start == "now":
            # Check if the stream_hls_url returns 200
            req_counter = 0
            while True:
                time.sleep(6)
                req_counter += 1
                if requests.get(stream_hls_url).status_code == 200:
                    logger_job.warning(f'{stream_hls_url} accessible after {req_counter} attempts.')
                    logger_job.warning(f'Waiting extra {enable_delay} seconds before we initiate the stream...')
                    time.sleep(enable_delay)
                    break
                if req_counter == 15:
                    logger_job.error(f'Stream {stream_name} cancelled after {req_counter} attempts.')
                    return
            scheduler.add_job(func=exec_stream, id=stream_id, args=(stream_id, stream_name, stream_prio, stream_hls_url))
        else:
            scheduler.add_job(
                func=exec_stream, trigger='cron', hour=stream_start, jitter=60,
                id=stream_id, args=(stream_id, stream_name, stream_prio, stream_hls_url)
            )
        database.update({stream_id: {'name': stream_name, 'start_at': stream_start, 'details': stream_details, 'src': stream_hls_url}})

        # Bootstrap the playhead if its still empty.
        if playhead == {}:
            fallback = fallback_search(database)
            scheduler.add_job(func=exec_stream, id='fallback', args=(fallback['stream_id'], fallback['stream_name'], 0, fallback['stream_hls_url']))

# Remove channel from the database
def remove_channel_from_database(database, scheduler, stream_id, stream_name, state):
    global prio
    global playhead
    if stream_id in database:
        logger_job.warning(f'{stream_id} ({stream_name}) will be removed. Reason: {state.exec}')
        database.pop(stream_id)
        try:
            scheduler.remove_job(stream_id)
        except Exception as e:
            logger_job.error(e)
        # Handle the situation where we remove an stream that is currently playing
        if stream_id == playhead['id']:
            logger_job.warning(f'{stream_id} was playing.')
            fallback = fallback_search(database)
            prio = 0
            logger_job.warning(f'Source priority is reset to 0')
            scheduler.add_job(func=exec_stream, id='fallback', args=(fallback['stream_id'], fallback['stream_name'], prio, fallback['stream_hls_url']))          

# Search for a fallback stream
def fallback_search(database):
    logger_job.warning('Searching for a fallback job.')
    current_hour = int(datetime.now().hour)
    scheduled_hours = []
    for key, value in database.items():
        if value['start_at'] == "now" or value['start_at'] == "never":
            # do not use non-time scheduled streams as fallbacks
            continue
        else:
            # append the hours in the working set
            scheduled_hours.append(int(value['start_at']))

            # convert the scheduled hours to a circular list
            scheduled_hours = scheduled_hours + [h + 24 for h in scheduled_hours]

            # find the closest scheduled hour
            closest_hour = min(scheduled_hours, key=lambda x: abs(x - current_hour))
        for key, value in database.items():
            if value['start_at'] == str(closest_hour % 24):
                fallback = { "stream_id": key,
                             "stream_name": value['name'],
                             "stream_hls_url": value['src']
                           }
                return fallback

# Update the playhead
def update_playhead(stream_id, stream_name, stream_prio, stream_hls_url):
    global playhead
    playhead = { "id": stream_id,
                 "name": stream_name,
                 "prio": stream_prio,
                 "head": stream_hls_url }
    logger_job.warning(f'Playhead: {str(playhead)}')

# Execute stream   
def exec_stream(stream_id, stream_name, stream_prio, stream_hls_url):
    global prio
    if stream_prio > prio:
        prio = stream_prio
        logger_job.warning(f'Source priority is now set to: {prio}')
        update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
    elif stream_prio == prio:
        update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
    elif stream_prio < prio:
        logger_job.warning(f'Source with higher priority ({prio}) is blocking. Skipping playhead update.') 

# Datarhei CORE API sync
def core_api_sync():
    global database
    
    new_ids = []
    try:
        process_list = client.v3_process_get_list()
    except Exception as e:
        logger_job.error(f'Error getting process list: {e}')
        return True
    for process in process_list:
        try:
            get_process = get_core_process_details(client, process.id)
            if not get_process:
                continue
            stream_id = get_process.reference
            meta = get_process.metadata
            state = get_process.state
        except Exception as e:
            logger_job.debug(process)
            continue
        
        if meta is None or meta['restreamer-ui'].get('meta') is None:
            # Skip processes without metadata or meta key
            continue
        
        new_ids.append(stream_id)
        stream_name = meta['restreamer-ui']['meta']['name']
        stream_description = meta['restreamer-ui']['meta']['description']
        stream_storage_type = meta['restreamer-ui']['control']['hls']['storage']
        stream_hls_url = f'https://{core_hostname}/{stream_storage_type}/{stream_id}.m3u8'

        if state.exec == "running":
            process_running_channel(database, scheduler, stream_id, stream_name, stream_description, stream_hls_url)
        else:
            remove_channel_from_database(database, scheduler, stream_id, stream_name, state)
            new_ids.remove(stream_id)

    # Cleanup orphaned references
    orphan_keys = [key for key in database if key not in new_ids]
    for orphan_key in orphan_keys:
        logger_job.warning(f'Key {orphan_key} is an orphan. Removing.')
        database.pop(orphan_key)
        scheduler.remove_job(orphan_key)

# Datarhei CORE API login
try:
    client = Client(base_url='https://' + core_hostname, username=core_username, password=core_password)
    logger_api.warning('Logging in to Datarhei Core API ' + core_username + '@' + core_hostname)
    client.login()
except Exception as e:
    logger_api.error('Client login error')
    logger_api.error(e)
    time.sleep(10)
    logger_api.error('Restarting...')
    sys.exit(1)
    
# Schedule API sync job
scheduler.add_job(func=core_api_sync, trigger='interval', seconds=core_sync_period, id='core_api_sync')
scheduler.get_job('core_api_sync').modify(next_run_time=datetime.now())

# Start the scheduler
scheduler.start()

### Flask ###
def client_address(req):
    if req.environ.get('HTTP_X_FORWARDED_FOR') is None:
        return req.environ['REMOTE_ADDR']
    else:
        # if behind a proxy
        return req.environ['HTTP_X_FORWARDED_FOR']

# Frontend
@app.route('/', methods=['GET'])
def root_route():
    # Get a list of video files and thumbnails
    video_files = [file for file in os.listdir(f'{rec_path}/vod/') if file.endswith(('.mp4', '.mkv', '.avi'))]
    thumbnails_path = f'{rec_path}/thumb/'
    thumbnails = [file for file in os.listdir(thumbnails_path) if file.endswith('.png')]
    # Get the full file paths
    thumbnail_paths = [os.path.join(thumbnails_path, file) for file in thumbnails]
    # Sort the file paths by modification time in reverse order
    sorted_thumbnails_paths = sorted(thumbnail_paths, key=lambda x: os.path.getmtime(x), reverse=True)
    # Extract file names from sorted paths
    sorted_thumbnails = [os.path.basename(file) for file in sorted_thumbnails_paths]
    thumbnails = [file for file in os.listdir(f'{rec_path}/thumb/') if file.endswith('.png')]
    logger_content.warning('[' + client_address(request) + '] index /')
    return render_template('index.html', now=datetime.utcnow(), video_files=video_files, thumbnails=sorted_thumbnails)

# JSON Data
@app.route('/playhead', methods=['GET'])
def playhead_route():
    global playhead
    return jsonify(playhead)

@app.route('/database', methods=['GET'])
def database_route():
    global database
    return jsonify(database)

# Images
@app.route("/thumb/<thumb_file>", methods=['GET'])
def thumb_route(thumb_file):
    thumb_path = f'{rec_path}/thumb/{thumb_file}'
    if not os.path.exists(thumb_path):
        abort(404)
    logger_content.warning('[' + client_address(request) + '] thumb' + str(thumb_path))
    return send_file(thumb_path, mimetype='image/png')

# Video uploader
@app.route('/video', methods=['POST'])
def video_upload():
    token = request.headers.get("Authorization")
    if token != "Bearer " + str(vod_token):
        return "Unauthorized", 401
    upload_path = f'{rec_path}/vod/'
    if not os.path.exists(upload_path):
        abort(404)
    # Streaming chunks
    #file = request.files['file']
    #if file:
    #    with open('large_file.txt', 'wb') as f:
    #        for chunk in file.stream:
    #            f.write(chunk)
    #    return 'File uploaded successfully'
    #return 'No file provided', 400
    file = request.files['file']
    filename = secure_filename(file.filename)
    file.save(os.path.join(upload_path, filename))
    return "File uploaded successfully"

# Video streamer
@app.route("/video/<video_file>", methods=['GET'])
def video_route(video_file):
    video_path = f'{rec_path}/vod/{video_file}'
    if not os.path.exists(video_path):
        abort(404)
    logger_content.warning('[' + client_address(request) + '] stream' + str(video_path))
    return send_file(video_path, mimetype='video/mp4')

# Video download
@app.route("/video/download/<video_file>", methods=['GET'])
def video_download_route(video_file):
    video_path = f'{rec_path}/vod/{video_file}'
    if not os.path.exists(video_path):
        abort(404)
    logger_content.warning('[' + client_address(request) + '] download' + str(video_path))
    return send_file(video_path, as_attachment=True, download_name=video_file)

# Video player
@app.route("/video/watch/<video_file_no_extension>", methods=['GET'])
def video_watch_route(video_file_no_extension):
    video_file = f'{video_file_no_extension}.mp4'
    thumb_file = f'{video_file_no_extension}.png'
    video_path = f'{rec_path}/vod/{video_file}'
    thumb_path = f'{rec_path}/thumb/{thumb_file}'
    if not os.path.exists(video_path):
        abort(404)
    if not os.path.exists(thumb_path):
        thumb_file = ""
    logger_content.warning('[' + client_address(request) + '] player' + str(video_path))
    return render_template('watch.html', video_file=video_file, thumb_file=thumb_file)

def create_app():
   return app
