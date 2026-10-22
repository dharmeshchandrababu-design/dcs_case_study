"""Small helpers shared by extract.py, load.py and label.py."""
import json
import logging
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
LOGS = DATA / "logs"


def warehouse_path():
    """DuckDB file; DCS_WAREHOUSE can point elsewhere (used for testing)."""
    return Path(os.environ.get("DCS_WAREHOUSE", DATA / "warehouse.duckdb"))


def load_env():
    """Read KEY=value lines from .env into the environment (if the file exists)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_config(name):
    with open(ROOT / "config" / f"{name}.yaml") as f:
        return yaml.safe_load(f)


def get_logger(name):
    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        for h in (logging.FileHandler(LOGS / f"{name}.log"), logging.StreamHandler()):
            h.setFormatter(fmt)
            logger.addHandler(h)
    return logger


def read_json(path, default):
    path = Path(path)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def write_json(path, obj):
    """Write to a temp file first, then rename, so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
