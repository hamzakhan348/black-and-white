#!/usr/bin/env python3
"""
========================================
MODEL DOWNLOADER — download_model.py
========================================
Pre-downloads DeOldify model weights to avoid delays on first use.
Supports multiple model types (artistic, stable) and both CPU/CUDA targets.

Usage:
    python download_model.py                      # artistic model, CPU
    python download_model.py --model stable        # stable model, CPU
    python download_model.py --model all --device cuda
"""

import os
import sys
import logging
import argparse

try:
    import torch
    from deoldify import device
    from deoldify.visualize import get_image_colorizer
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Make sure you've installed requirements.txt first:")
    print("    pip install -r requirements.txt")
    sys.exit(1)

# ==================== SECTION 1: Logging Setup ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== SECTION 2: Model Registry ====================
MODEL_TYPES = {
    'artistic': True,
    'stable': False
}

# ==================== SECTION 3: Download Logic ====================
def resolve_device(device_type):
    """Fall back to CPU with a warning if CUDA was requested but isn't available."""
    if device_type == 'cuda' and not torch.cuda.is_available():
        logger.warning("CUDA requested but not available on this machine. Falling back to CPU.")
        return 'cpu'
    return device_type

def download_model(model_type, device_type):
    """Download a single DeOldify model and cache it locally."""
    logger.info(f"Downloading DeOldify '{model_type}' model...")
    logger.info(f"Using device: {device_type}")

    device.set(device=device_type)

    try:
        get_image_colorizer(
            artistic=MODEL_TYPES[model_type],
            render_factor=35
        )
        cache_dir = os.path.join(os.path.expanduser('~'), '.cache', 'torch', 'hub')
        logger.info(f"Model '{model_type}' downloaded and cached successfully.")
        logger.info(f"Cache location: {cache_dir}")
        return True
    except Exception as e:
        logger.error(f"Failed to download '{model_type}' model: {e}")
        return False

def download_models(model_choice, device_type):
    """Download one model or all models, depending on --model value."""
    device_type = resolve_device(device_type)
    targets = list(MODEL_TYPES.keys()) if model_choice == 'all' else [model_choice]

    results = {}
    for model_type in targets:
        results[model_type] = download_model(model_type, device_type)

    return results

# ==================== SECTION 4: CLI Entry Point ====================
def main():
    parser = argparse.ArgumentParser(description='Download DeOldify model weights.')
    parser.add_argument(
        '--model',
        choices=['artistic', 'stable', 'all'],
        default='artistic',
        help='Model type to download (default: artistic)'
    )
    parser.add_argument(
        '--device',
        choices=['cpu', 'cuda'],
        default='cpu',
        help='Device to use for downloading (default: cpu)'
    )
    args = parser.parse_args()

    results = download_models(args.model, args.device)

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        logger.error(f"Failed to download: {', '.join(failed)}")
        sys.exit(1)

    logger.info("All requested models downloaded successfully.")
    sys.exit(0)

if __name__ == '__main__':
    main()
