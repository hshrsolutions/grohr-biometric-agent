"""Offline tests. No server, no hardware, no network.

	python3 -m tests.test_agent      (from the repository root)
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grohr_agent.config import ConfigError, load  # noqa: E402
from grohr_agent.devices.zkteco import ZKTecoReader  # noqa: E402
from grohr_agent.server import backoff  # noqa: E402
from grohr_agent.spool import Spool, punch_key  # noqa: E402

_results = []


def check(label, condition, detail=""):
	_results.append((label, bool(condition), detail))
	print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  — ' + str(detail) if detail and not condition else ''}")


def _tmp(name):
	return os.path.join(tempfile.mkdtemp(prefix="grohr-agent-test-"), name)


def _punch(user_id, minute):
	return {
		"device_serial": "SN-1",
		"device_user_id": user_id,
		"punch_time": f"2026-08-31 09:{minute:02d}:00",
		"raw_log_type": "0",
	}


# ------------------------------------------------------------------- config


def test_config():
	print("Config:")
	path = _tmp("config.json")
	with open(path, "w") as f:
		json.dump({"site_url": "https://x.example/", "api_key": "k", "api_secret": "s", "connector": "C"}, f)
	config = load(path)
	check("defaults are applied", config.batch_size == 200 and config.poll_interval_seconds == 300)
	check("the trailing slash is trimmed", config.site_url == "https://x.example")
	check("relative paths resolve beside the config", os.path.isabs(config.spool_path))

	with open(path, "w") as f:
		json.dump({"site_url": "https://x.example", "api_key": "k"}, f)
	try:
		load(path)
		check("missing keys are refused", False)
	except ConfigError as e:
		check("missing keys are refused", "api_secret" in str(e) and "connector" in str(e), str(e))

	with open(path, "w") as f:
		f.write("{not json")
	try:
		load(path)
		check("invalid JSON is refused with a readable message", False)
	except ConfigError as e:
		check("invalid JSON is refused with a readable message", "not valid JSON" in str(e), str(e))

	try:
		load(_tmp("nope.json"))
		check("a missing config file is refused", False)
	except ConfigError as e:
		check("a missing config file is refused", "config.example.json" in str(e), str(e))


# -------------------------------------------------------------------- spool


def test_spool():
	print("Spool:")
	spool = Spool(_tmp("spool.sqlite3"))

	check("adding punches returns how many were new", spool.add([_punch("1", 1), _punch("2", 2)]) == 2)
	check("re-adding the same punches adds nothing", spool.add([_punch("1", 1), _punch("2", 2)]) == 0)
	check("the key matches the server's formula",
	      punch_key("SN-1", "1", "2026-08-31 09:01:00") == punch_key(" SN-1 ", " 1 ", "2026-08-31 09:01:00"))

	batch = spool.take(10)
	check("take returns what is pending", len(batch) == 2)
	spool.accept([batch[0]["key"]])
	check("accepted punches are forgotten", spool.counts()["pending"] == 1)

	# A punch the server keeps refusing must not block the queue forever.
	remaining = [b["key"] for b in spool.take(10)]
	for _ in range(3):
		spool.defer(remaining, "boom", max_attempts=3)
	counts = spool.counts()
	check("an unsendable punch is dead-lettered, not retried forever",
	      counts["pending"] == 0 and counts["dead"] == 1, counts)
	spool.close()


def test_spool_survives_restart():
	print("Spool durability:")
	path = _tmp("spool.sqlite3")
	first = Spool(path)
	first.add([_punch("7", 7)])
	first.close()
	second = Spool(path)
	check("punches survive the process dying", second.counts()["pending"] == 1)
	second.close()


# ------------------------------------------------------------------ backoff


def test_backoff():
	print("Back-off:")
	check("it grows", backoff(1) < backoff(3) < backoff(5))
	check("it is capped", backoff(50) == 600, backoff(50))
	check("the first retry is quick", backoff(0) <= 10, backoff(0))


# ------------------------------------------------------------------- reader


class _Record:
	def __init__(self, uid, user_id, timestamp, punch=0):
		self.uid, self.user_id, self.timestamp, self.punch, self.status = uid, user_id, timestamp, punch, 1


class _Conn:
	def __init__(self, records):
		self.records, self.calls = records, []

	def get_attendance(self):
		self.calls.append("get_attendance")
		return self.records

	def disable_device(self):
		self.calls.append("disable_device")

	def clear_attendance(self):
		self.calls.append("clear_attendance")

	def disconnect(self):
		self.calls.append("disconnect")


class _Reader(ZKTecoReader):
	def __init__(self, device, logger, conn):
		super().__init__(device, logger)
		self.conn = conn

	def connect(self):
		return self.conn


class _Log:
	def __getattr__(self, _):
		return lambda *a, **k: None


def test_reader():
	print("Reader:")
	base = datetime(2026, 8, 31, 9, 0)
	conn = _Conn([_Record(1, "1434", base), _Record(2, "1530", base + timedelta(minutes=10), punch=1)])
	punches = _Reader({"serial_no": "SN-1", "ip_address": "192.0.2.1"}, _Log(), conn).read()
	check("records become punch dictionaries", len(punches) == 2)
	check("timestamps are formatted for the wire", punches[0]["punch_time"] == "2026-08-31 09:00:00",
	      punches[0]["punch_time"])
	check("the reader is never taken off the door", "disable_device" not in conn.calls, conn.calls)
	check("device memory is never cleared", "clear_attendance" not in conn.calls, conn.calls)
	check("the connection is closed", "disconnect" in conn.calls)

	later = _Reader({"serial_no": "SN-1", "ip_address": "192.0.2.1"}, _Log(), _Conn(conn.records)).read(
		since=base
	)
	check("the server's cursor filters what is read", len(later) == 1, later)


def run():
	print("GroHR biometric agent — offline tests\n")
	test_config()
	test_spool()
	test_spool_survives_restart()
	test_backoff()
	test_reader()

	failed = [r for r in _results if not r[1]]
	print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
	if failed:
		for label, _, detail in failed:
			print(f"  - {label}  {detail}")
		return 1
	print("All good.")
	return 0


if __name__ == "__main__":
	sys.exit(run())
