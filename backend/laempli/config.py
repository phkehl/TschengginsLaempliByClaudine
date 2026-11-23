"""Configuration: YAML file, see backend/tschenggins-laempli.yaml.example"""

import logging
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit("yaml missing, please run: sudo apt install python3-yaml")

from .devices import valid_id
from .gitlab import KEEP_DAYS, signing_key

LOG = logging.getLogger(__name__)

# Keys, default values and types
DEFAULTS = {
    "listen": "127.0.0.1",
    "port": 8070,
    "ws_port": None,
    "data": "../data",
    "heartbeat": 15,
    "log_level": "info",
    "gitlab_signing_tokens": [],
    "github_webhook_secrets": [],
    "generic_tokens": [],
    "gitlab_ignored_projects": [],
    "github_ignored_repos": [],
    "events_keep_days": KEEP_DAYS,
    "housekeeping_interval": 3600,
    "firmware_keep": 10,
    "devices_keep_days": 365,
    "editors": [],
    "device_editors": {},
}

# Old keys: ignored, with a warning
OBSOLETE = {"prefix": "the proxy sends X-Forwarded-Prefix"}

# Integer keys: smallest value
INT_MIN = {"events_keep_days": 1, "housekeeping_interval": 60, "firmware_keep": 1, "devices_keep_days": 0}

LOG_LEVELS = ("debug", "info", "warning", "error")


class ConfigError(Exception):
    """Bad configuration"""


def load_config(path):
    """Load configuration file

    A relative data path is relative to the directory of the configuration file.

    Returns a dict with all keys of DEFAULTS. Raises ConfigError.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as ex:
        raise ConfigError("Cannot load {}: {}".format(path, ex)) from ex
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("{}: not a mapping".format(path))
    for key in sorted(set(raw) & set(OBSOLETE)):
        LOG.warning("%s: %s is not used, remove it (%s)", path, key, OBSOLETE[key])
        del raw[key]
    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise ConfigError("{}: unknown keys: {}".format(path, ", ".join(map(str, unknown))))

    cfg = dict(DEFAULTS)
    cfg.update(raw)
    for key in ("listen", "data", "log_level"):
        if not isinstance(cfg[key], str):
            raise ConfigError("{}: {} must be a string".format(path, key))
    if (not isinstance(cfg["port"], int)) or (cfg["port"] < 1) or (cfg["port"] > 65535):
        raise ConfigError("{}: port must be 1..65535".format(path))
    if cfg["ws_port"] is None:
        cfg["ws_port"] = ((cfg["port"] + 1) if (cfg["port"] < 65535) else (cfg["port"] - 1))
    if isinstance(cfg["ws_port"], bool) or (not isinstance(cfg["ws_port"], int)) or (cfg["ws_port"] < 1) or \
            (cfg["ws_port"] > 65535) or (cfg["ws_port"] == cfg["port"]):
        raise ConfigError("{}: ws_port must be 1..65535, not port".format(path))
    if isinstance(cfg["heartbeat"], bool) or (not isinstance(cfg["heartbeat"], (int, float))) or \
            (cfg["heartbeat"] <= 0):
        raise ConfigError("{}: heartbeat must be > 0".format(path))
    if cfg["log_level"] not in LOG_LEVELS:
        raise ConfigError("{}: log_level must be one of {}".format(path, ", ".join(LOG_LEVELS)))
    if not cfg["data"]:
        raise ConfigError("{}: data must not be empty".format(path))
    for key, low in INT_MIN.items():
        if isinstance(cfg[key], bool) or (not isinstance(cfg[key], int)) or (cfg[key] < low):
            raise ConfigError("{}: {} must be an integer >= {}".format(path, key, low))

    tokens = cfg["gitlab_signing_tokens"]
    if tokens is None:
        tokens = []
    if not isinstance(tokens, list):
        raise ConfigError("{}: gitlab_signing_tokens must be a list".format(path))
    for ix, token in enumerate(tokens):
        try:
            signing_key(token)
        except ValueError as ex:
            raise ConfigError("{}: gitlab_signing_tokens[{}]: {}".format(path, ix, ex)) from ex
    cfg["gitlab_signing_tokens"] = tokens

    secrets = cfg["github_webhook_secrets"]
    if secrets is None:
        secrets = []
    if not isinstance(secrets, list):
        raise ConfigError("{}: github_webhook_secrets must be a list".format(path))
    for ix, secret in enumerate(secrets):
        if (not isinstance(secret, str)) or (not secret):
            raise ConfigError("{}: github_webhook_secrets[{}] must be a non-empty string".format(path, ix))
    cfg["github_webhook_secrets"] = secrets

    tokens = cfg["generic_tokens"]
    if tokens is None:
        tokens = []
    if not isinstance(tokens, list):
        raise ConfigError("{}: generic_tokens must be a list".format(path))
    for ix, token in enumerate(tokens):
        if (not isinstance(token, str)) or (not token):
            raise ConfigError("{}: generic_tokens[{}] must be a non-empty string".format(path, ix))
    cfg["generic_tokens"] = tokens

    for key in ("gitlab_ignored_projects", "github_ignored_repos"):
        names = cfg[key]
        if names is None:
            names = []
        if not isinstance(names, list):
            raise ConfigError("{}: {} must be a list".format(path, key))
        for ix, name in enumerate(names):
            if (not isinstance(name, str)) or (not name.strip()):
                raise ConfigError("{}: {}[{}] must be a non-empty string".format(path, key, ix))
        cfg[key] = [name.strip() for name in names]

    users = cfg["editors"]
    if users is None:
        users = []
    if not isinstance(users, list):
        raise ConfigError("{}: editors must be a list".format(path))
    for ix, user in enumerate(users):
        if (not isinstance(user, str)) or (not user):
            raise ConfigError("{}: editors[{}] must be a non-empty string".format(path, ix))
    cfg["editors"] = users

    # User -> device IDs: that user can edit these devices
    dev_users = cfg["device_editors"]
    if dev_users is None:
        dev_users = {}
    if not isinstance(dev_users, dict):
        raise ConfigError("{}: device_editors must be a mapping".format(path))
    for user, ids in dev_users.items():
        if (not isinstance(user, str)) or (not user):
            raise ConfigError("{}: device_editors: user names must be non-empty strings".format(path))
        if not isinstance(ids, list):
            raise ConfigError("{}: device_editors.{} must be a list".format(path, user))
        for ix, dev_id in enumerate(ids):
            if not valid_id(dev_id):
                raise ConfigError("{}: device_editors.{}[{}] must be a device ID".format(path, user, ix))
    cfg["device_editors"] = {user: sorted(set(ids)) for user, ids in dev_users.items()}

    base = os.path.dirname(os.path.abspath(path))
    cfg["data"] = os.path.normpath(os.path.join(base, cfg["data"]))
    return cfg
