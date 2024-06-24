import sys
import os
import time
import logging
import json
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, abort
from flask.helpers import send_file
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client
from ffmpeg import FFmpeg, Progress

app = Flask(__name__)
scheduler = BackgroundScheduler()

# Log handlers
logger_api = logging.getLogger('waitress')
logger_job = logging.getLogger('apscheduler')
log_level = os.environ.get('SCHEDULER_LOG_LEVEL', 'INFO').upper()
logger_api.setLevel(log_level)
logger_job.setLevel(log_level)

# Variables
scheduler_hostname = os.environ.get('SCHEDULER_API_HOSTNAME', 'tv.example.com')
core_sync_period = int(os.environ.get('CORE_SYNC_PERIOD', 15))
api_hostname = os.environ.get('CORE_API_HOSTNAME', 'stream.example.com')
api_username = os.environ.get('CORE_API_AUTH_USERNAME', 'admin')
api_password = os.environ.get('CORE_API_AUTH_PASSWORD', 'pass')
rec_path = "/recordings"
enable_delay = 24

# Init
database = {}
playhead = {}
rechead = {}
prio = 0

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
    
# Process a running channel
def process_running_channel(database, scheduler, stream_id, stream_name, stream_description, stream_hls_url):
    global recording
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
        logger_job.warning(f'{stream_id} ({stream_name}) has been registered.')
        if stream_start == "now":
            logger_job.warning("Stream should start now. Preparing")
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
            if stream_prio == 2:
                rec_id = f'rec_{stream_id}'
                scheduler.add_job(func=exec_recorder, id=rec_id, args=(stream_id, stream_name, stream_hls_url))
        else:
            logger_job.warning(f"Stream start hour is set to {stream_start}")
            scheduler.add_job(
                func=exec_stream, trigger='cron', hour=stream_start, jitter=60,
                id=stream_id, args=(stream_id, stream_name, stream_prio, stream_hls_url)
            )
        database.update({stream_id: {'name': stream_name, 'start_at': stream_start, 'meta': stream_description, 'src': stream_hls_url}})
        # Bootstrap the playhead if its still empty.
        if playhead == {}:
            fallback = fallback_search(database)
            scheduler.add_job(func=exec_stream, id='fallback', args=(fallback['stream_id'], fallback['stream_name'], 0, fallback['stream_hls_url']))

# Remove channel from the database
def remove_channel_from_database(database, scheduler, stream_id, stream_name, state):
    global prio
    global playhead
    global rechead
    if stream_id in database:
        logger_job.warning(f'{stream_id} ({stream_name}) will be removed. Reason: {state.exec}')
        database.pop(stream_id)
        try:
            scheduler.remove_job(stream_id)
        except Exception as joberror:
            logger_job.error(joberror)
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
    current_hour = datetime.now().hour
    scheduled_hours = []
    for key, value in database.items():
        if value['start_at'] == "now" or value['start_at'] == "never":
            # do not use non-time scheduled streams as fallbacks
            continue
        else:
            # append the hours in the working set
            scheduled_hours.append(value['start_at'])

            # convert the scheduled hours to a circular list
            scheduled_hours = scheduled_hours + [h + 24 for int(h) in scheduled_hours]

            # find the closest scheduled hour
            closest_hour = min(scheduled_hours, key=lambda x: abs(x - current_hour))
        for key, value in database.items():
            if value['start_at'] == str(closest_hour % 24):
                fallback = { "stream_id": key,
                             "stream_name": value['name'],
                             "stream_hls_url": value['src']
                           }
                return fallback

# Find a matching stream name within epg.json
def find_event_entry(epg, stream_name):
    for entry in epg:
        if "name" in entry and entry["name"] == stream_name:
            return {"start_at": entry.get("start_at"), "prio": entry.get("prio")}
    return None

# Update the playhead
def update_playhead(stream_id, stream_name, stream_prio, stream_hls_url):
    global playhead
    playhead = { "id": stream_id,
                 "name": stream_name,
                 "prio": stream_prio,
                 "head": stream_hls_url }
    logger_job.warning(f'Playhead position is: {str(playhead)}')

# Execute stream   
def exec_stream(stream_id, stream_name, stream_prio, stream_hls_url):
    global prio
    logger_job.warning(f'Hello {stream_name}! :]')
    if stream_prio > prio:
        prio = stream_prio
        logger_job.warning(f'Source priority is now set to: {prio}')
        update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
    elif stream_prio == prio:
        update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
    elif stream_prio < prio:
        logger_job.warning(f'Source with higher priority ({prio}) is blocking. Skipping playhead update.') 

# Execute recorder
def exec_recorder(stream_id, stream_name, stream_hls_url):
    global rechead
    current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S-%f")
    video_file = current_datetime + ".mp4"
    thumb_file = current_datetime + ".png"
    if rechead != {}:
        logger_job.error('Recorder is already started. Refusing to start another job.')
    else:
        logger_job.warning(f'Recording {video_file} started.')
        rechead = { 'id': stream_id,
                    'name': stream_name,
                    'video': video_file,
                    'thumb': thumb_file }
        video_output = f'{rec_path}/live/{video_file}'
        thumb_output = f'{rec_path}/live/{thumb_file}'
        
        try:
            # Record a mp4 file
            ffmpeg = (
                FFmpeg()
                .option("y")
                .input(stream_hls_url)
                .output(video_output,
                        {"codec:v": "copy", "codec:a": "copy", "bsf:a": "aac_adtstoasc"},
                ))
            @ffmpeg.on("progress")
            def on_progress(progress: Progress):
                print(progress)
            ffmpeg.execute()
            logger_job.warning(f'Recording of {video_file} finished.')

        except Exception as joberror:
            logger_job.error(f'Recording of {video_file} failed!')
            logger_job.error(joberror)

        else:
            # Show Metadata
            ffmpeg_metadata = (
                FFmpeg(executable="ffprobe")
                .input(video_output,
                       print_format="json",
                       show_streams=None,)
            )
            media = json.loads(ffmpeg_metadata.execute())
            logger_job.warning(f"# Video")
            logger_job.warning(f"- Codec: {media['streams'][0]['codec_name']}")
            logger_job.warning(f"- Resolution: {media['streams'][0]['width']} X {media['streams'][0]['height']}")
            logger_job.warning(f"- Duration: {media['streams'][0]['duration']}")
            logger_job.warning(f"# Audio")
            logger_job.warning(f"- Codec: {media['streams'][1]['codec_name']}")
            logger_job.warning(f"- Sample Rate: {media['streams'][1]['sample_rate']}")
            logger_job.warning(f"- Duration: {media['streams'][1]['duration']}")
        
            thumb_skip_time = float(media['streams'][0]['duration']) // 2
            thumb_width = media['streams'][0]['width'] 
    
            # Generate thumbnail image from the recorded mp4 file
            ffmpeg_thumb = (
                FFmpeg()
                .input(video_output, ss=thumb_skip_time)
                .output(thumb_output, vf='scale={}:{}'.format(thumb_width, -1), vframes=1)
            )
            ffmpeg_thumb.execute()
            logger_job.warning(f'Thumbnail {thumb_file} created.')
        
            # When ready, move the recorded from the live dir to the archives and reset the rec head
            os.rename(f'{video_output}', f'{rec_path}/vod/{video_file}')
            os.rename(f'{thumb_output}', f'{rec_path}/thumb/{thumb_file}')

        finally:
            # Reset the rechead
            time.sleep(5)
            rechead = {}
            logger_job.warning(f'Rechead reset.')

# Datarhei CORE API sync
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
            logger_job.debug(process)
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
        logger_job.warning(f'Key {orphan_key} is an orphan. Removing.')
        database.pop(orphan_key)
        scheduler.remove_job(orphan_key)

# Datarhei CORE API login
try:
    client = Client(base_url='https://' + api_hostname, username=api_username, password=api_password)
    logger_api.warning('Logging in to Datarhei Core API ' + api_username + '@' + api_hostname)
    client.login()
except Exception as err:
    logger_api.error('Client login error')
    logger_api.error(err)
    time.sleep(10)
    logger_api.error('Restarting...')
    sys.exit(1)
    
# Schedule sync jobs
scheduler.add_job(func=core_api_sync, trigger='interval', seconds=core_sync_period, id='core_api_sync')
scheduler.get_job('core_api_sync').modify(next_run_time=datetime.now())

# Start the scheduler
scheduler.start()

# Flask API
@app.route('/', methods=['GET'])
def root_route():
    about_json = { 'about': 'DeflaxTV API' }
    return jsonify(about_json)

# JSON data
@app.route('/playhead', methods=['GET'])
def playhead_route():
    global playhead
    return jsonify(playhead)

@app.route('/rechead', methods=['GET'])
def rechead_route():
    global rechead
    return jsonify(rechead)

@app.route('/database', methods=['GET'])
def database_route():
    global database
    return jsonify(database)

# Images
@app.route("/img/<file_name>", methods=['GET'])
def img_route(file_name):
    reqfile = f'./img/{file_name}'
    if not os.path.exists(reqfile):
        abort(404)
    return send_file(reqfile, mimetype='image/png')

@app.route("/thumb/<file_name>", methods=['GET'])
def thumb_route(file_name):
    reqfile = f'{rec_path}/thumb/{file_name}'
    if not os.path.exists(reqfile):
        abort(404)
    return send_file(reqfile, mimetype='image/png')

# Video
@app.route("/video/<file_name>", methods=['GET'])
def video_route(file_name):
    reqfile = f'{rec_path}/vod/{file_name}'
    if not os.path.exists(reqfile):
        abort(404)
    return send_file(reqfile, mimetype='video/mp4')

@app.route("/video/download/<file_name>", methods=['GET'])
def video_download_route(file_name):
    reqfile = f'{rec_path}/vod/{file_name}'
    if not os.path.exists(reqfile):
        abort(404)
    return send_file(reqfile, as_attachment=True, download_name=file_name)

@app.route('/video/watch/<file_name_no_extension>', methods=['GET'])
def video_watch_route(file_name_no_extension):
    video_file = f'{file_name_no_extension}.mp4'
    thumb_file = f'{file_name_no_extension}.png'
    video_path = f'{rec_path}/vod/{video_file}'
    thumb_path = f'{rec_path}/thumb/{thumb_file}'
    if not os.path.exists(video_path):
        abort(404)
    if not os.path.exists(thumb_path):
        thumb_file = ""
    return render_template('watch.html', video_file=video_file, thumb_file=thumb_file)

# Gallery
@app.route("/gallery", methods=['GET'])
def gallery_route():
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
    return render_template('gallery.html', video_files=video_files, thumbnails=sorted_thumbnails)

def create_app():
   return app
