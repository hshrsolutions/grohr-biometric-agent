"""A reader with no hardware behind it.

Reads punches from a local JSON file instead of a device. Two real uses:

* commissioning — prove the whole pipe works (credentials, network, mapping,
  checkins) before anyone has mounted a reader or run a cable;
* support — reproduce a customer's problem without their hardware.

The file is a list of objects: `device_user_id`, `punch_time`, and optionally
`raw_log_type`. The serial comes from the device record, as it would from a real
reader.
"""

import json
import os
from datetime import datetime


class FileReader:
	driver_name = "Fake"

	def __init__(self, device, logger):
		self.device = device
		self.log = logger

	def source_path(self):
		return os.environ.get("GROHR_AGENT_FAKE_PUNCHES", "punches.json")

	def read(self, since: datetime | None = None):
		path = self.source_path()
		if not os.path.exists(path):
			self.log.warning(f"Fake reader: no punch file at {os.path.abspath(path)}.")
			return []
		with open(path, encoding="utf-8") as handle:
			rows = json.load(handle)

		punches = []
		for row in rows:
			punch_time = str(row["punch_time"])
			if since and _parse(punch_time) and _parse(punch_time) <= since:
				continue
			punches.append(
				{
					"device_serial": self.device["serial_no"],
					"device_user_id": str(row["device_user_id"]),
					"punch_time": punch_time,
					"raw_log_type": row.get("raw_log_type"),
					"payload": {"source": "fake"},
				}
			)
		punches.sort(key=lambda p: p["punch_time"])
		return punches


def _parse(value):
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
		try:
			return datetime.strptime(value, fmt)
		except ValueError:
			continue
	return None
