# Utils/software_loader.py
"""Read and filter software.yml."""
import os
import yaml
from Utils.utils import logger

# Project root = two levels up from this file (Utils/software_loader.py → project root)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_yml_path(yml_path: str) -> str:
    # If the caller passed a relative path, resolve it from the project root
    if not os.path.isabs(yml_path):
        yml_path = os.path.join(_PROJECT_ROOT, yml_path)
    return yml_path


def _read_yml(yml_path: str) -> dict:
    yml_path = _resolve_yml_path(yml_path)

    try:
        with open(yml_path, "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error(f"software.yml not found: {yml_path}")
        return {}


def _entry_name(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        if "name" in entry:
            return str(entry["name"])
        if "software_name" in entry:
            return str(entry["software_name"])
        if len(entry) == 1:
            return str(next(iter(entry.keys())))
    return None


def load_software(yml_path: str, category: str = "ALL") -> list[str]:
    data = _read_yml(yml_path)
    if not data:
        return []

    if category.upper() == "ALL":
        items = []
        for v in data.values():
            if isinstance(v, list):
                items.extend([name for item in v if (name := _entry_name(item))])
            elif isinstance(v, dict):
                items.extend(str(name) for name in v.keys())
        return items

    category_data = data.get(category)
    if not category_data:
        logger.warning(f"Category '{category}' not found in {_resolve_yml_path(yml_path)}")
        return []

    if isinstance(category_data, list):
        return [name for item in category_data if (name := _entry_name(item))]
    return list(category_data)


def load_software_metadata(yml_path: str, category: str = "ALL") -> dict[str, dict]:
    data = _read_yml(yml_path)
    if not data:
        return {}

    categories = data.values() if category.upper() == "ALL" else [data.get(category, {})]
    metadata: dict[str, dict] = {}
    for category_data in categories:
        if isinstance(category_data, list):
            for item in category_data:
                name = _entry_name(item)
                if not name:
                    continue
                if isinstance(item, dict):
                    if "name" in item or "software_name" in item:
                        details = {k: v for k, v in item.items() if k not in {"name", "software_name"}}
                    elif len(item) == 1:
                        details = next(iter(item.values())) or {}
                    else:
                        details = {}
                    metadata[name] = details if isinstance(details, dict) else {}
                else:
                    metadata.setdefault(name, {})
        elif isinstance(category_data, dict):
            for name, details in category_data.items():
                metadata[str(name)] = details if isinstance(details, dict) else {}
    return metadata
