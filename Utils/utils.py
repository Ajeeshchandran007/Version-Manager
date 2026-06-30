# utils/utils.py
import logging
import logging.handlers
import yaml
import json
import pandas as pd
import os
import re
from dotenv import load_dotenv

# Load .env from project root once at import time
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(_ENV_PATH)

def setup_logging():
    log_file = "email_version_mcp.log"
    log_level = logging.DEBUG

    logger = logging.getLogger("email_version_mcp")
    logger.setLevel(log_level)

    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)

        rotating_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1024*1024*5, backupCount=5
        )
        rotating_handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        console_handler.setFormatter(formatter)
        rotating_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(rotating_handler)

    return logger

logger = setup_logging()

def _expand_env_vars(config: dict) -> dict:
    """Recursively replace ${VAR_NAME} placeholders with environment variable values."""
    if isinstance(config, dict):
        return {k: _expand_env_vars(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_expand_env_vars(i) for i in config]
    elif isinstance(config, str):
        def replacer(match):
            var = match.group(1)
            value = os.environ.get(var)
            if value is None:
                logger.warning(f"Environment variable '{var}' not set.")
            return value or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replacer, config)
    return config


def _find_unresolved_placeholders(value, path="config"):
    unresolved = []
    if isinstance(value, dict):
        for k, v in value.items():
            unresolved.extend(_find_unresolved_placeholders(v, f"{path}.{k}"))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            unresolved.extend(_find_unresolved_placeholders(v, f"{path}[{i}]"))
    elif isinstance(value, str) and re.search(r'\$\{[^}]+\}', value):
        unresolved.append(path)
    return unresolved

def config_mtime() -> float:
    """Return config.json modified time for lightweight hot-reload checks."""
    try:
        return os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return 0.0


def load_config():
    # Reload .env as well so config placeholders reflect recent operational edits.
    load_dotenv(_ENV_PATH, override=True)
    config_path = _CONFIG_PATH
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        config = _expand_env_vars(config)
        unresolved = _find_unresolved_placeholders(config)
        if unresolved:
            logger.warning(
                "Unresolved config placeholders found at: %s",
                ", ".join(unresolved),
            )
        logger.info("Configuration loaded successfully.")
        return config
    except FileNotFoundError:
        logger.error(f"Config file not found at {config_path}")
        raise
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {config_path}")
        raise

def filter_software_yml(input_path, output_path, category):
    try:
        with open(input_path, 'r') as f:
            data = yaml.safe_load(f)

        filtered_data = {}
        if category.lower() == 'all':
            filtered_data = data
            logger.info("Filtering software for category: ALL")
        elif category in data:
            filtered_data[category] = data[category]
            logger.info(f"Filtering software for category: {category}")
        else:
            logger.warning(f"Category '{category}' not found in {input_path}")
            return False

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            yaml.dump(filtered_data, f)
        logger.info(f"Filtered software written to {output_path}")
        return True
    except FileNotFoundError:
        logger.error(f"Input software YML file not found at {input_path}")
        return False
    except Exception as e:
        logger.error(f"Error filtering software YML: {e}")
        return False

def read_software_yml(file_path):
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
        software_list = []
        if data:
            for category, items in data.items():
                if isinstance(items, list):
                    software_list.extend(items)
                elif isinstance(items, dict):
                    software_list.extend(items.keys())
        logger.info(f"Read {len(software_list)} software items from {file_path}")
        return software_list
    except FileNotFoundError:
        logger.error(f"Software YML file not found at {file_path}")
        return []
    except Exception as e:
        logger.error(f"Error reading software YML: {e}")
        return []
