"""The loop.

The agent is dumb on purpose. It holds no business logic, decides nothing about
employees, shifts or log types, and stores no state that matters. It reads
punches and posts them. Every rule lives on the server, where it can be fixed
without physical access to a customer's premises — which is the whole lesson of
the PowerShell script this replaces.
"""

import time
from datetime import datetime

from grohr_agent import __version__
from grohr_agent.config import source_for
from grohr_agent.devices import SQL_DRIVERS, reader_for
from grohr_agent.server import AuthError, Server, ServerError, backoff
from grohr_agent.spool import Spool


class Agent:
	def __init__(self, config, logger):
		self.config = config
		self.log = logger
		self.server = Server(config, logger)
		self.spool = Spool(config.spool_path)
		self.failures = 0
		self.devices = []

	# --------------------------------------------------------------- one cycle

	def cycle(self):
		"""One pass: refresh the device list, read, send, report in. Never raises
		— a cycle that fails is a cycle, not the end of the agent."""
		try:
			self.refresh_devices()
			read = self.read_devices()
			sent = self.drain()
			self.heartbeat()
			self.spool.prune_sent(self.config.sent_retention_days)
			self.failures = 0
			counts = self.spool.counts()
			self.log.info(
				f"Cycle complete: {read} read, {sent} accepted, "
				f"{counts['pending']} pending, {counts['dead']} dead-lettered."
			)
			return True
		except AuthError as e:
			# Not transient. Say so loudly and keep the punches.
			self.failures += 1
			self.log.error(f"AUTHENTICATION FAILED — {e}")
			return False
		except ServerError as e:
			self.failures += 1
			self.log.warning(f"Server unreachable: {e}. {self.spool.counts()['pending']} punches held locally.")
			return False
		except Exception as e:
			self.failures += 1
			self.log.exception(f"Unexpected error in cycle: {type(e).__name__}: {e}")
			return False

	def refresh_devices(self):
		"""The device list comes from the server, so adding a reader is a desk
		action rather than a site visit."""
		boot = self.server.bootstrap() or {}
		self.devices = boot.get("devices") or []
		if not boot.get("ingestion_enabled", 1):
			self.log.warning("Ingestion is disabled on the server; punches will be held locally.")
		self.log.debug(f"Bootstrap: {len(self.devices)} device(s).")

	def read_devices(self):
		total = 0
		for device in self.devices:
			reader_class = reader_for(device.get("driver"))
			if not reader_class:
				self.log.warning(
					f"No reader for driver {device.get('driver')!r} on device {device.get('name')}; skipping."
				)
				continue
			if device.get("driver") in SQL_DRIVERS:
				source = source_for(self.config, device)
				if not source:
					self.log.error(
						f"{device.get('name')}: driver {device.get('driver')} reads a database, but config.json "
						f"has no `sources` entry for {device.get('name')!r}, serial {device.get('serial_no')!r} or \"*\"."
					)
					continue
				device = {**device, "source": source}
			try:
				# The cursor is the server's, not ours. A stale one is harmless.
				since = _parse(device.get("last_punch_time"))
				punches = reader_class(device, self.log).read(since=since)
				added = self.spool.add(punches)
				total += added
				self.log.info(
					f"{device['name']}: {len(punches)} punch(es) read, {added} new"
					f"{f' (since {since})' if since else ''}."
				)
			except Exception as e:
				# One unreachable reader must not stop the others.
				self.log.error(f"{device.get('name')}: could not read — {type(e).__name__}: {e}")
		return total

	def drain(self):
		"""Send what is spooled, in batches, until the spool is empty or the
		server stops accepting."""
		sent = 0
		while True:
			batch = self.spool.take(self.config.batch_size)
			if not batch:
				break
			try:
				result = self.server.push([item["punch"] for item in batch])
			except ServerError as e:
				died = self.spool.defer([i["key"] for i in batch], e, self.config.max_attempts)
				if died:
					self.log.error(
						f"{len(died)} punch(es) dead-lettered after {self.config.max_attempts} attempts. "
						"They are in the 'dead' table of the spool and have NOT reached the server."
					)
				raise

			# A 200 means the server has staged every punch in the batch, whatever
			# it went on to decide about each one. Ours to forget.
			self.spool.accept([item["key"] for item in batch])
			sent += len(batch)
			self._log_outcome(result)

			if len(batch) < self.config.batch_size:
				break
		return sent

	def _log_outcome(self, result):
		if not isinstance(result, dict):
			return
		unmapped = result.get("unmapped", 0)
		errors = result.get("errors", 0)
		message = (
			f"Server accepted {result.get('received', 0)}: "
			f"{result.get('mapped', 0)} mapped, {result.get('duplicates', 0)} duplicate, "
			f"{unmapped} unmapped, {errors} error."
		)
		if unmapped or errors:
			# Not our problem to retry — the server is holding them in its queue.
			self.log.warning(message + " Unmapped and errored punches are in the server's work queue.")
		else:
			self.log.info(message)

	def heartbeat(self):
		"""Sent every cycle, punches or not. Silence has to mean something."""
		serials = [d["serial_no"] for d in self.devices if d.get("serial_no")]
		reply = self.server.heartbeat(__version__, serials) or {}
		requests_ = reply.get("profile_requests") if isinstance(reply, dict) else None
		if requests_:
			self.answer_profiles(requests_)
		pushes = reply.get("user_pushes") if isinstance(reply, dict) else None
		if pushes:
			self.apply_user_pushes(pushes)

	def apply_user_pushes(self, pushes):
		"""The server wants these employees created, updated or removed in Easy Time
		Pro. Without an `easytime` block in config.json we say so instead of guessing;
		either way the punch cycle is never affected."""
		from grohr_agent import easytime

		settings = self.config.get("easytime")
		if not settings or not settings.get("url"):
			results = [{"sync_id": p.get("sync_id"), "outcome": "Error",
			            "message": "This agent has no `easytime` block in config.json."} for p in pushes]
		else:
			results = easytime.run(settings, pushes, self.log)
		for r in results:
			self.log.info(f"Easy Time Pro {r.get('emp_code') or ''}: {r.get('outcome')} {r.get('message') or ''}".rstrip())
		try:
			self.server.user_push_results(results)
		except Exception as e:
			# The server re-sends unanswered requests; the API calls are idempotent.
			self.log.warning(f"Could not report Easy Time Pro results: {type(e).__name__}: {e}")

	def answer_profiles(self, ids):
		"""The server wants names and photos for these IDs (HR just set or changed
		them). A failure here is logged and never costs the punch cycle."""
		from grohr_agent.profiles import lookup

		try:
			found = lookup(self.config, [str(i) for i in ids][:50], self.log)
		except Exception as e:
			self.log.error(f"Profile lookup failed: {type(e).__name__}: {e}")
			found = [{"device_user_id": i, "found": False, "error": f"{type(e).__name__}: {e}"[:300]} for i in ids[:50]]
		try:
			result = self.server.profiles(found)
			self.log.info(f"Profiles sent for {len(found)} ID(s): {result}")
		except Exception as e:
			self.log.warning(f"Could not send profiles: {type(e).__name__}: {e}")

	# ------------------------------------------------------------------- loop

	def run_forever(self, stop=None):
		self.log.info(f"GroHR biometric agent {__version__} starting — connector {self.config.connector!r}.")
		if self.config.get("_insecure"):
			self.log.warning("site_url is plain http; the API secret will cross the network unencrypted.")

		while not (stop and stop()):
			started = time.time()
			ok = self.cycle()
			delay = self.config.poll_interval_seconds if ok else backoff(self.failures)
			elapsed = time.time() - started
			for _ in range(int(max(0, delay - elapsed))):
				if stop and stop():
					break
				time.sleep(1)
		self.log.info("Agent stopping.")
		self.spool.close()


def _parse(value):
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
		try:
			return datetime.strptime(str(value), fmt)
		except ValueError:
			continue
	return None
