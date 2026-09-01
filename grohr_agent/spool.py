"""The offline spool.

A punch read from a device is written here before any attempt to send it, and
removed only once the server has accepted it. A site outage, a flat network or a
restart therefore costs nothing.

This is not a cursor and not a source of truth — the cursor lives on the server
(PRD §7.2 item 1). If the spool is lost, the agent simply re-reads from the
server's cursor and the idempotency key absorbs the overlap. That is the whole
reason the agent is allowed to be this simple.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS punch (
	key         TEXT PRIMARY KEY,
	payload     TEXT NOT NULL,
	attempts    INTEGER NOT NULL DEFAULT 0,
	first_seen  TEXT NOT NULL,
	last_error  TEXT
);
CREATE TABLE IF NOT EXISTS dead (
	key         TEXT PRIMARY KEY,
	payload     TEXT NOT NULL,
	attempts    INTEGER NOT NULL,
	first_seen  TEXT NOT NULL,
	died_at     TEXT NOT NULL,
	last_error  TEXT
);
"""


def punch_key(device_serial, device_user_id, punch_time):
	"""Mirrors the server's key exactly, so local dedupe and server dedupe agree."""
	raw = f"{(device_serial or '').strip()}|{(device_user_id or '').strip()}|{punch_time}"
	return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class Spool:
	def __init__(self, path):
		os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
		self.db = sqlite3.connect(path)
		self.db.row_factory = sqlite3.Row
		self.db.executescript(SCHEMA)
		self.db.commit()

	def close(self):
		self.db.close()

	def add(self, punches):
		"""Returns how many were new. A punch already spooled is not re-added —
		re-reading the same device window is normal, not an error."""
		added = 0
		now = datetime.now().isoformat(timespec="seconds")
		for punch in punches:
			key = punch_key(punch["device_serial"], punch["device_user_id"], punch["punch_time"])
			cursor = self.db.execute(
				"INSERT OR IGNORE INTO punch (key, payload, first_seen) VALUES (?, ?, ?)",
				(key, json.dumps(punch), now),
			)
			added += cursor.rowcount
		self.db.commit()
		return added

	def take(self, limit):
		rows = self.db.execute(
			"SELECT key, payload, attempts FROM punch ORDER BY first_seen LIMIT ?", (limit,)
		).fetchall()
		return [{"key": r["key"], "punch": json.loads(r["payload"]), "attempts": r["attempts"]} for r in rows]

	def accept(self, keys):
		"""The server has it. Ours to forget."""
		if not keys:
			return
		self.db.executemany("DELETE FROM punch WHERE key = ?", [(k,) for k in keys])
		self.db.commit()

	def defer(self, keys, error, max_attempts):
		"""A transient failure. Count the attempt; dead-letter anything that has
		exhausted its patience, so one poisoned punch cannot block the queue
		behind it forever."""
		if not keys:
			return []
		self.db.executemany(
			"UPDATE punch SET attempts = attempts + 1, last_error = ? WHERE key = ?",
			[(str(error)[:500], k) for k in keys],
		)
		self.db.commit()
		died = [r["key"] for r in self.db.execute(
			"SELECT key FROM punch WHERE attempts >= ?", (max_attempts,)
		).fetchall()]
		if died:
			now = datetime.now().isoformat(timespec="seconds")
			self.db.execute(
				"INSERT OR REPLACE INTO dead (key, payload, attempts, first_seen, died_at, last_error) "
				"SELECT key, payload, attempts, first_seen, ?, last_error FROM punch WHERE attempts >= ?",
				(now, max_attempts),
			)
			self.db.execute("DELETE FROM punch WHERE attempts >= ?", (max_attempts,))
			self.db.commit()
		return died

	def counts(self):
		pending = self.db.execute("SELECT COUNT(*) AS n FROM punch").fetchone()["n"]
		dead = self.db.execute("SELECT COUNT(*) AS n FROM dead").fetchone()["n"]
		return {"pending": pending, "dead": dead}
