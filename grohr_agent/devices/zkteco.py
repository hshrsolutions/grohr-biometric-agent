"""ZKTeco / eSSL over the LAN, via pyzk.

Two things it does not do, both of them upstream behaviours:

* it never calls `disable_device()` — that takes the reader off the door for the
  length of the fetch, which on a device with a backlog is a real outage;
* it never calls `clear_attendance()` — upstream's own comment calls that a
  data-loss risk.
"""

from datetime import datetime


class ZKTecoReader:
	driver_name = "ZKTeco"

	def __init__(self, device, logger):
		self.device = device
		self.log = logger

	def connect(self):
		"""Overridden in tests."""
		from zk import ZK

		zk = ZK(
			self.device["ip_address"],
			port=int(self.device.get("port") or 4370),
			timeout=30,
		)
		return zk.connect()

	def read(self, since: datetime | None = None):
		conn = None
		try:
			conn = self.connect()
			records = conn.get_attendance() or []
		finally:
			if conn:
				try:
					conn.disconnect()
				except Exception:
					pass

		punches = []
		for record in records:
			timestamp = getattr(record, "timestamp", None)
			if timestamp is None:
				continue
			if since and timestamp <= since:
				continue
			punches.append(
				{
					"device_serial": self.device["serial_no"],
					"device_user_id": str(getattr(record, "user_id", "")).strip(),
					"punch_time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
					"raw_log_type": None if getattr(record, "punch", None) is None else str(record.punch),
					"payload": {
						"uid": getattr(record, "uid", None),
						"status": getattr(record, "status", None),
						"punch": getattr(record, "punch", None),
					},
				}
			)
		punches.sort(key=lambda p: p["punch_time"])
		return punches
