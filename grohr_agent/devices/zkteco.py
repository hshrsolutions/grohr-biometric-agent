"""ZKTeco / eSSL terminals over the LAN, directly, via pyzk — no attendance
software in between. Built and checked against the LX50 family (TCP 4370), and
the same protocol serves the other ZK "push SDK" terminals (K40, F18, MB160, …).

Two things it does not do, both of them upstream behaviours:

* it never calls `disable_device()` — that takes the reader off the door for the
  length of the fetch, which on a device with a backlog is a real outage;
* it never calls `clear_attendance()` — upstream's own comment calls that a
  data-loss risk.

It also never writes: no users, no time sync, no settings.

Per-device options live in config.json under `zkteco`, keyed by device name,
serial or "*" (see `settings_for`). Nothing there is needed for a terminal with
no comm key on port 4370.
"""

from datetime import datetime, timedelta

DEFAULTS = {
	# The terminal's Comm Key (Menu > Comm. > Security). 0 = none.
	"password": 0,
	"timeout": 30,
	# Some LX50 firmware / routed links only answer UDP.
	"force_udp": False,
	# Skip pyzk's ICMP ping before connecting (needed when ping is blocked).
	"ommit_ping": False,
	# Re-read this far before the server cursor; the spool's sent-ledger stops
	# a re-read punch being sent twice.
	"lookback_minutes": 1440,
	# Refuse a terminal whose serial is not the one registered in GroHR — a
	# wrong or re-used IP address would otherwise file punches under the wrong
	# device.
	"verify_serial": True,
	# Only for --probe-zk / overrides; normally the server supplies these.
	"ip_address": None,
	"port": None,
}

# Device clock further from the PC clock than this is worth a warning (it skews
# every punch). The agent never sets the device clock itself.
CLOCK_DRIFT_WARN_SECONDS = 300


def settings_for(config, device):
	"""Options for one device: by device name, then serial, then "*"; defaults
	underneath. `config` may be None (tests, probe)."""
	table = (config or {}).get("zkteco") or {}
	chosen = {}
	for key in ("*", device.get("serial_no"), device.get("name")):
		if key and isinstance(table.get(key), dict):
			chosen.update(table[key])
	return {**DEFAULTS, **{k: v for k, v in chosen.items() if not str(k).startswith("_")}}


class SerialMismatch(Exception):
	pass


class ZKTecoReader:
	driver_name = "ZKTeco"

	def __init__(self, device, logger):
		self.device = device
		self.log = logger
		self.opts = {**DEFAULTS, **(device.get("zk") or {})}

	# -- connection ---------------------------------------------------------

	def connect(self):
		"""Overridden in tests."""
		from zk import ZK

		zk = ZK(
			self.opts.get("ip_address") or self.device["ip_address"],
			port=int(self.opts.get("port") or self.device.get("port") or 4370),
			timeout=int(self.opts["timeout"]),
			password=int(self.opts["password"] or 0),
			force_udp=bool(self.opts["force_udp"]),
			ommit_ping=bool(self.opts["ommit_ping"]),
		)
		return zk.connect()

	@staticmethod
	def _safe(conn, method, default=None):
		"""Optional info calls: older firmware answers some of them with an
		error. Never let that cost us the punches."""
		try:
			return getattr(conn, method)()
		except Exception:
			return default

	# -- reading ------------------------------------------------------------

	def read(self, since: datetime | None = None):
		conn = None
		try:
			conn = self.connect()
			self._check_serial(conn)
			self._check_clock(conn)
			records = conn.get_attendance() or []
		finally:
			if conn:
				try:
					conn.disconnect()
				except Exception:
					pass

		floor = None
		if since:
			floor = since - timedelta(minutes=int(self.opts["lookback_minutes"] or 0))

		punches = []
		for record in records:
			timestamp = getattr(record, "timestamp", None)
			if timestamp is None:
				continue
			if floor and timestamp < floor:
				continue
			user_id = str(getattr(record, "user_id", "")).strip()
			if not user_id:
				continue
			punches.append(
				{
					"device_serial": self.device["serial_no"],
					"device_user_id": user_id,
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

	def _check_serial(self, conn):
		expected = str(self.device.get("serial_no") or "").strip()
		actual = self._safe(conn, "get_serialnumber")
		if not (self.opts["verify_serial"] and expected and actual):
			return
		# Some firmware pads or prefixes the serial ("SN:..."); compare loosely.
		actual = str(actual).replace("\x00", "").strip()
		if actual.upper().lstrip("SN:").strip() != expected.upper().lstrip("SN:").strip():
			raise SerialMismatch(
				f"{self.device.get('ip_address')} answered as serial {actual!r}, but GroHR has "
				f"{expected!r}. Not reading it (fix the IP address, or set `verify_serial` false)."
			)

	def _check_clock(self, conn):
		device_time = self._safe(conn, "get_time")
		if not device_time or not self.log:
			return
		drift = abs((datetime.now() - device_time).total_seconds())
		if drift > CLOCK_DRIFT_WARN_SECONDS:
			self.log.warning(
				f"{self.device.get('name')}: the terminal clock is {int(drift // 60)} min away from this "
				"PC. Punch times are the terminal's own; set the terminal clock."
			)

	# -- commissioning ------------------------------------------------------

	def probe(self, out=print):
		"""Read-only: connect and describe the terminal. Returns True if it
		answered. Changes nothing on the device."""
		conn = None
		try:
			conn = self.connect()
		except Exception as e:
			out(f"  FAILED to connect: {type(e).__name__}: {e}")
			out("  Check: IP/port (default 4370), same network, Comm Key (`password`), "
			    "and try `force_udp` / `ommit_ping` in config.json > zkteco.")
			return False
		try:
			get = lambda m: self._safe(conn, m)
			out(f"  serial:    {get('get_serialnumber')}")
			out(f"  device:    {get('get_device_name')}  ({get('get_platform')})")
			out(f"  firmware:  {get('get_firmware_version')}")
			out(f"  MAC:       {get('get_mac')}")
			device_time = get("get_time")
			if device_time:
				drift = int((datetime.now() - device_time).total_seconds())
				out(f"  clock:     {device_time:%Y-%m-%d %H:%M:%S}  (PC differs by {drift:+d}s)")
			try:
				conn.read_sizes()  # pyzk stores the counts on the connection
				out(f"  users:     {getattr(conn, 'users', '?')} / {getattr(conn, 'users_cap', '?')}")
				out(f"  punches:   {getattr(conn, 'records', '?')} / {getattr(conn, 'rec_cap', '?')}")
			except Exception:
				out("  users/punches: not reported by this firmware")
			serial = str(get("get_serialnumber") or "").strip()
			expected = str(self.device.get("serial_no") or "").strip()
			if expected and serial and serial.upper() != expected.upper():
				out(f"  NOTE: GroHR/config serial is {expected!r} — register this terminal under {serial!r}.")
			return True
		finally:
			try:
				conn.disconnect()
			except Exception:
				pass
