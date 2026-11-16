"""Configuration.

Inert JSON, never executable Python. Upstream BAST keeps credentials in
`local_config.py`, an importable module: a typo there is a crash, the file is
arbitrary code, and the secrets sit in plaintext in something that looks like
source. This is a data file, validated on load, that fails fast and says what is
wrong.
"""

import json
import os

REQUIRED = ("site_url", "api_key", "api_secret", "connector")

DEFAULTS = {
	"poll_interval_seconds": 300,
	"batch_size": 200,
	"max_attempts": 10,
	"request_timeout_seconds": 60,
	"verify_tls": True,
	"proxy": None,
	"log_dir": "logs",
	"spool_path": "spool.sqlite3",
	# For sites reached by IP or through a load balancer that does not rewrite it.
	"host_header": None,
	# How long a delivered punch is remembered locally, so a re-read of the
	# look-back window does not send it again.
	"sent_retention_days": 45,
	# Database sources, keyed by Biometric Device name or serial, or "*" for
	# every database-backed device. Credentials live here and nowhere else.
	"sources": {},
}

SOURCE_ENGINES = ("mysql", "mariadb", "mssql", "sqlserver")


class ConfigError(Exception):
	pass


class Config(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key)


def load(path="config.json"):
	if not os.path.exists(path):
		raise ConfigError(
			f"No config file at {os.path.abspath(path)}. "
			"Copy config.example.json to config.json and fill it in."
		)
	try:
		with open(path, encoding="utf-8") as handle:
			raw = json.load(handle)
	except json.JSONDecodeError as e:
		raise ConfigError(f"{path} is not valid JSON: {e}")

	missing = [key for key in REQUIRED if not raw.get(key)]
	if missing:
		raise ConfigError(f"{path} is missing: {', '.join(missing)}")

	config = Config({**DEFAULTS, **raw})
	config["site_url"] = config["site_url"].rstrip("/")

	if config["batch_size"] < 1 or config["batch_size"] > 1000:
		raise ConfigError("batch_size must be between 1 and 1000.")
	if config["poll_interval_seconds"] < 10:
		raise ConfigError("poll_interval_seconds must be at least 10.")
	if not config["site_url"].startswith(("http://", "https://")):
		raise ConfigError("site_url must start with http:// or https://")
	if config["site_url"].startswith("http://") and "localhost" not in config["site_url"] \
			and "127.0.0.1" not in config["site_url"]:
		# Not fatal — some sites are reached over a private link — but it must be
		# said out loud, because an API secret is going over this wire.
		config["_insecure"] = True

	if not isinstance(config["sources"], dict):
		raise ConfigError("sources must be an object keyed by device name, serial or \"*\".")
	for key, source in config["sources"].items():
		if key.startswith("_"):
			continue
		if not isinstance(source, dict):
			raise ConfigError(f"sources.{key} must be an object.")
		engine = str(source.get("engine") or "mysql").lower()
		if engine not in SOURCE_ENGINES:
			raise ConfigError(f"sources.{key}.engine must be 'mysql' or 'mssql', not {engine!r}.")
		if not source.get("connection_string") and not source.get("host"):
			raise ConfigError(f"sources.{key} needs a host (or, for SQL Server, a connection_string).")

	# Relative paths are relative to the config file, not to wherever the service
	# happened to be started from.
	base = os.path.dirname(os.path.abspath(path))
	for key in ("log_dir", "spool_path"):
		if not os.path.isabs(config[key]):
			config[key] = os.path.join(base, config[key])

	return config


def source_for(config, device):
	"""The local database settings for a server-listed device: by device name,
	then serial, then the "*" catch-all. None if nothing matches."""
	sources = config.get("sources") or {}
	for key in (device.get("name"), device.get("serial_no"), "*"):
		if key and isinstance(sources.get(key), dict):
			return sources[key]
	return None
