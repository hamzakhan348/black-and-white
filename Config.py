"""
Configuration module for the colorization web app.

Loads settings from environment variables (optionally via a .env file)
or falls back to sensible defaults for local development.

Usage:
    from config import Config
    app.config.from_object(Config)
"""

import os
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars can still be set manually
    pass


# -------------------- Environment --------------------
ENV = os.environ.get('FLASK_ENV', 'development')
IS_PRODUCTION = ENV == 'production'

# -------------------- Paths --------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
RESULT_FOLDER = os.path.join(BASE_DIR, 'results')
LOG_FOLDER = os.path.join(BASE_DIR, 'logs')
MODEL_FOLDER = os.path.join(BASE_DIR, 'models')  # not strictly needed for DeOldify

# Create directories if missing
for folder in [UPLOAD_FOLDER, RESULT_FOLDER, LOG_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# -------------------- Server Settings --------------------
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
HOST = os.environ.get('HOST', '0.0.0.0')

try:
    PORT = int(os.environ.get('PORT', 5000))
except ValueError:
    raise ValueError(
        "Invalid PORT environment variable — must be an integer."
    )

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError(
            "SECRET_KEY environment variable must be set in production. "
            "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
        )
    SECRET_KEY = 'dev-secret-change-in-production'
    logging.warning(
        "Using insecure default SECRET_KEY. Set the SECRET_KEY env var for production."
    )

# -------------------- Upload Limits --------------------
MAX_CONTENT_LENGTH = 32 * 1024 * 1024  # 32 MB
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'bmp', 'tiff', 'webp'}
MAX_IMAGE_DIMENSION = 2048  # max width/height before resizing to avoid OOM

# -------------------- DeOldify Settings --------------------
DEFAULT_RENDER_FACTOR = int(os.environ.get('DEFAULT_RENDER_FACTOR', 35))
RENDER_FACTOR_MIN = 10
RENDER_FACTOR_MAX = 50
ARTISTIC_MODEL = os.environ.get('ARTISTIC_MODEL', 'True').lower() == 'true'

if not (RENDER_FACTOR_MIN <= DEFAULT_RENDER_FACTOR <= RENDER_FACTOR_MAX):
    raise ValueError(
        f"DEFAULT_RENDER_FACTOR must be between {RENDER_FACTOR_MIN} and {RENDER_FACTOR_MAX}"
    )

# -------------------- Job Queue Settings --------------------
JOB_TIMEOUT = int(os.environ.get('JOB_TIMEOUT', 300))  # seconds before job considered stale
CLEANUP_INTERVAL = int(os.environ.get('CLEANUP_INTERVAL', 3600))  # seconds between cleanup runs

# -------------------- Logging --------------------
LOG_LEVEL = logging.DEBUG if DEBUG else logging.INFO
LOG_FILE = os.path.join(LOG_FOLDER, 'app.log')
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'


def configure_logging():
    """Set up root logger with console + file handlers. Call once at app startup."""
    logging.basicConfig(
        level=LOG_LEVEL,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE),
        ],
)
