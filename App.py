"""
========================================
MAIN APPLICATION SERVER — app.py
========================================
Flask application for colorizing black-and-white images using DeOldify.
Supports multiple file uploads, progress tracking, job queue, and cleanup.
"""

import os
import sys
import time
import uuid
import base64
import logging
import threading
import queue
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, request, render_template, jsonify, send_file, url_for, abort
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from PIL import Image, UnidentifiedImageError
import torch

from deoldify import device
from deoldify.visualize import get_image_colorizer

import config

# ==================== SECTION 1: Logging Setup ====================
logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(config.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("Starting colorization app...")

# ==================== SECTION 2: Flask App Init ====================
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['UPLOAD_FOLDER'] = config.UPLOAD_FOLDER
app.config['RESULT_FOLDER'] = config.RESULT_FOLDER

# Only load the heavy model in the actual worker process, not the
# Werkzeug reloader's watcher process (prevents loading it twice in debug mode).
IS_MAIN_PROCESS = os.environ.get('WERKZEUG_RUN_MAIN') != 'true' or not config.DEBUG

# ==================== SECTION 3: Device Setup ====================
def setup_device():
    """Detect CUDA and configure DeOldify device."""
    if torch.cuda.is_available():
        device.set(device='cuda')
        logger.info("GPU detected - using CUDA")
        return 'cuda'
    device.set(device='cpu')
    logger.info("No GPU found - using CPU (may be slow)")
    return 'cpu'

DEVICE = setup_device()

# ==================== SECTION 4: Model Loading ====================
colorizer = None
if IS_MAIN_PROCESS:
    logger.info("Loading DeOldify artistic model...")
    try:
        colorizer = get_image_colorizer(
            artistic=config.ARTISTIC_MODEL,
            render_factor=config.DEFAULT_RENDER_FACTOR
        )
        logger.info("Model loaded successfully.")
    except Exception as e:
        logger.critical(f"Failed to load DeOldify model: {e}")
        sys.exit(1)

# ==================== SECTION 5: Job Store ====================
# job structure:
# {
#   'id': str, 'original_filename': str, 'input_path': str,
#   'status': 'queued'|'processing'|'completed'|'failed'|'cancelled',
#   'progress': int (0-100), 'result_path': str or None,
#   'error': str or None, 'render_factor': int,
#   'created_at': datetime, 'updated_at': datetime, 'user_id': None
# }
jobs = {}
job_queue = queue.Queue()
job_lock = threading.Lock()

def utcnow():
    """Timezone-aware UTC now (datetime.utcnow() is deprecated)."""
    return datetime.now(timezone.utc)

# ==================== SECTION 6: Background Worker ====================
def worker():
    """Background thread that pulls job IDs off the queue and processes them."""
    logger.info("Worker thread started.")
    while True:
        job_id = job_queue.get()
        if job_id is None:
            break
        process_job(job_id)
        job_queue.task_done()
    logger.info("Worker thread exiting.")

def process_job(job_id):
    """Colorize a single queued job and save the result to disk."""
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            logger.warning(f"Job {job_id} not found, skipping.")
            return
        if job['status'] != 'queued':
            logger.info(f"Job {job_id} already processed/cancelled, skipping.")
            return
        job['status'] = 'processing'
        job['updated_at'] = utcnow()

    logger.info(f"Processing job {job_id} for {job['original_filename']}")

    try:
        input_path = job['input_path']
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        try:
            pil_img = Image.open(input_path).convert('RGB')
        except (UnidentifiedImageError, OSError) as e:
            raise ValueError(f"Invalid image file: {e}")

        max_dim = config.MAX_IMAGE_DIMENSION
        if max(pil_img.size) > max_dim:
            pil_img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            logger.info(f"Resized image to {pil_img.size}")

        with job_lock:
            job['progress'] = 20

        render_factor = job.get('render_factor', config.DEFAULT_RENDER_FACTOR)
        result = colorizer.get_transformed_image(pil_img, render_factor=render_factor)

        with job_lock:
            job['progress'] = 80

        output_filename = f"colorized_{job['original_filename']}"
        output_path = os.path.join(config.RESULT_FOLDER, f"{job_id}_{output_filename}")
        result.save(output_path, format='JPEG', quality=95)
        logger.info(f"Saved result to {output_path}")

        with job_lock:
            job['result_path'] = output_path
            job['status'] = 'completed'
            job['progress'] = 100
            job['updated_at'] = utcnow()
            logger.info(f"Job {job_id} completed successfully.")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        with job_lock:
            job['status'] = 'failed'
            job['error'] = str(e)
            job['updated_at'] = utcnow()
    finally:
        try:
            if os.path.exists(job['input_path']):
                os.unlink(job['input_path'])
                logger.debug(f"Removed temp input {job['input_path']}")
        except Exception as e:
            logger.warning(f"Could not delete temp file {job['input_path']}: {e}")

if IS_MAIN_PROCESS:
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

# ==================== SECTION 7: Cleanup Worker ====================
def cleanup_worker():
    """Periodically remove stale completed/failed jobs to free disk space."""
    while True:
        time.sleep(config.CLEANUP_INTERVAL)
        try:
            cleanup_old_jobs()
        except Exception as e:
            logger.error(f"Cleanup thread error: {e}")

def cleanup_old_jobs():
    """Delete jobs (and their files) older than JOB_TIMEOUT seconds."""
    stale_threshold = utcnow() - timedelta(seconds=config.JOB_TIMEOUT)
    removed = 0
    with job_lock:
        to_delete = [
            jid for jid, job in jobs.items()
            if job['status'] in ('completed', 'failed', 'cancelled')
            and job['updated_at'] < stale_threshold
        ]
        for job_id in to_delete:
            job = jobs[job_id]
            for path_key in ('result_path', 'input_path'):
                path = job.get(path_key)
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception as e:
                        logger.warning(f"Could not delete {path_key} {path}: {e}")
            del jobs[job_id]
            removed += 1
    if removed:
        logger.info(f"Removed {removed} stale jobs.")

# Now actually started — this was previously commented out and never ran.
if IS_MAIN_PROCESS:
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()

# ==================== SECTION 8: Helper Functions ====================
def allowed_file(filename):
    """Check whether the file extension is in the allowed set."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in config.ALLOWED_EXTENSIONS

def validate_image(file_stream):
    """Try to open/verify the image to confirm it's a real, valid image file."""
    try:
        img = Image.open(file_stream)
        img.verify()
        return True
    except Exception:
        return False

def get_job(job_id):
    """Thread-safe lookup of a job by ID."""
    with job_lock:
        return jobs.get(job_id)

def safe_filename(filename):
    """secure_filename() can return '' for filenames that are only special
    characters (e.g. '???.jpg'). Fall back to a generic name so we never
    save a file with an empty name."""
    name = secure_filename(filename)
    return name if name else f"upload_{uuid.uuid4().hex[:8]}.jpg"

# ==================== SECTION 9: Routes — Pages ====================
@app.route('/')
def index():
    """Render the main upload/result page."""
    return render_template('index.html')

# ==================== SECTION 10: Routes — Upload ====================
@app.route('/api/upload', methods=['POST'])
def upload():
    """
    Accept one or more image files, validate ALL of them first, then create
    and queue a job for each. Expects multipart/form-data with 'images'
    and an optional 'render_factor'.
    """
    if 'images' not in request.files:
        return jsonify({'error': 'No files uploaded'}), 400

    files = request.files.getlist('images')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'Empty filenames'}), 400

    render_factor = request.form.get('render_factor', config.DEFAULT_RENDER_FACTOR, type=int)
    render_factor = max(config.RENDER_FACTOR_MIN, min(config.RENDER_FACTOR_MAX, render_factor))

    # Validate every file BEFORE saving any of them, so a bad file in the
    # batch doesn't leave earlier files saved/queued with no way to undo it.
    for file in files:
        if file.filename == '':
            continue
        if not allowed_file(file.filename):
            return jsonify({'error': f'File type not allowed: {file.filename}'}), 400
        if not validate_image(file.stream):
            return jsonify({'error': f'Invalid image file: {file.filename}'}), 400
        file.stream.seek(0)

    job_ids = []
    for file in files:
        if file.filename == '':
            continue

        original_filename = safe_filename(file.filename)
        job_id = str(uuid.uuid4())
        temp_path = os.path.join(config.UPLOAD_FOLDER, f"{job_id}_{original_filename}")
        file.save(temp_path)

        with job_lock:
            jobs[job_id] = {
                'id': job_id,
                'original_filename': original_filename,
                'input_path': temp_path,
                'status': 'queued',
                'progress': 0,
                'result_path': None,
                'error': None,
                'render_factor': render_factor,
                'created_at': utcnow(),
                'updated_at': utcnow(),
                'user_id': None
            }
        job_queue.put(job_id)
        job_ids.append(job_id)

    return jsonify({'job_ids': job_ids, 'count': len(job_ids)})

# ==================== SECTION 11: Routes — Status ====================
           @app.route('/api/status/<job_id>')
def status(job_id):
    """Return status/progress for one job. Includes a download URL once
    completed (base64 image data is no longer inlined here — see note below)."""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    response = {
        'id': job['id'],
        'status': job['status'],
        'progress': job['progress'],
        'original_filename': job['original_filename'],
        'error': job.get('error'),
        'created_at': job['created_at'].isoformat(),
        'updated_at': job['updated_at'].isoformat()
    }

    if job['status'] == 'completed' and job.get('result_path'):
        response['download_url'] = url_for('download_result', job_id=job_id, _external=True)

    return jsonify(response)

@app.route('/api/status/all')
def status_all():
    """Return a lightweight status summary for every job in memory."""
    with job_lock:
        all_jobs = list(jobs.values())

    response = []
    for job in all_jobs:
        resp = {
            'id': job['id'],
            'original_filename': job['original_filename'],
            'status': job['status'],
            'progress': job['progress'],
            'error': job.get('error'),
            'updated_at': job['updated_at'].isoformat()
        }
        if job['status'] == 'completed' and job.get('result_path'):
            resp['download_url'] = url_for('download_result', job_id=job['id'], _external=True)
        response.append(resp)
    return jsonify(response)

# ==================== SECTION 12: Routes — Download ====================
@app.route('/api/download/<job_id>')
def download_result(job_id):
    """Serve the colorized image as a downloadable file."""
    job = get_job(job_id)
    if not job:
        abort(404, description="Job not found")
    if job['status'] != 'completed' or not job.get('result_path'):
        abort(404, description="Result not available")
    if not os.path.exists(job['result_path']):
        abort(404, description="Result file missing")

    return send_file(
        job['result_path'],
        as_attachment=True,
        download_name=f"colorized_{job['original_filename']}",
        mimetype='image/jpeg'
    )

@app.route('/api/preview/<job_id>')
def preview_result(job_id):
    """Return the completed image as base64 — call this once, not on a poll loop."""
    job = get_job(job_id)
    if not job or job['status'] != 'completed' or not job.get('result_path'):
        return jsonify({'error': 'Result not available'}), 404
    try:
        with open(job['result_path'], 'rb') as f:
            img_data = f.read()
        return jsonify({'result_base64': base64.b64encode(img_data).decode('utf-8')})
    except Exception as e:
        logger.error(f"Failed to read result for {job_id}: {e}")
        return jsonify({'error': 'Failed to read result'}), 500

# ==================== SECTION 13: Routes — Job Management ====================
@app.route('/api/delete/<job_id>', methods=['DELETE'])
def delete_job(job_id):
    """Delete a single job and its files."""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    for path_key in ('result_path', 'input_path'):
        path = job.get(path_key)
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except Exception as e:
                logger.warning(f"Could not delete {path_key}: {e}")

    with job_lock:
        del jobs[job_id]

    return jsonify({'message': f'Job {job_id} deleted.'})

@app.route('/api/cleanup', methods=['POST'])
def cleanup_all():
    """Manually delete all completed/failed/cancelled jobs and their files."""
    removed = 0
    with job_lock:
        to_delete = [jid for jid, job in jobs.items()
                     if job['status'] in ('completed', 'failed', 'cancelled')]
        for job_id in to_delete:
            job = jobs[job_id]
            for path_key in ('result_path', 'input_path'):
                path = job.get(path_key)
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
            del jobs[job_id]
            removed += 1
    return jsonify({'message': f'Cleaned up {removed} jobs.'})

# ==================== SECTION 14: Error Handlers ====================
@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(e):
    return jsonify({'error': 'File too large. Max size: 32 MB.'}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Resource not found.'}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({'error': 'Internal server error.'}), 500

# ==================== SECTION 15: Health Check ====================
@app.route('/health')
def health():
    """Basic liveness/readiness probe."""
    return jsonify({'status': 'ok', 'device': DEVICE, 'jobs': len(jobs)})

# ==================== SECTION 16: Entry Point ====================
if __name__ == '__main__':
    logger.info(f"Starting Flask server on {config.HOST}:{config.PORT}")
    app.run(
        debug=config.DEBUG,
        host=config.HOST,
        port=config.PORT,
        threaded=True,
        use_reloader=False  # prevents the model from being loaded twice
          )
