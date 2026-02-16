import os

from quart import abort, request
from werkzeug.utils import secure_filename


def register_ingest_routes(app, config) -> None:
    """Register ingestion/upload API routes."""

    @app.route('/video', methods=['POST'])
    async def video_upload():
        """Handle video file uploads."""
        token = request.headers.get('Authorization')
        if token != f'Bearer {config.vod_token}':
            return 'Unauthorized', 401

        upload_path = os.path.join(config.rec_path, 'vod')
        if not os.path.exists(upload_path):
            abort(404)

        files = await request.files
        if 'file' not in files:
            return 'No file provided', 400

        file = files['file']
        if file.filename == '':
            return 'No file selected', 400

        filename = secure_filename(file.filename)
        await file.save(os.path.join(upload_path, filename))
        return 'File uploaded successfully', 200
