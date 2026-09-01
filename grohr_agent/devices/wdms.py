"""eSSL Time Pro / WDMS — reading the vendor's own database.

Some sites have no reachable reader at all: the hardware talks only to the
vendor's Windows software, which writes punches into a SQL Server database. The
punches are there; the device is not on offer. So this reader queries that
database directly.

**It is strictly read-only.** It issues one SELECT and writes nothing — no
status column, no flag, no schema change. That matters more than it sounds:
the integration this replaces at one site required adding columns to the
vendor's table to track what had been sent, which meant a schema change on
software nobody present owned or supported. Here the cursor lives on the GroHR
server and duplicates are absorbed by the idempotency key, so the vendor's
database is never touched.

Column names vary between Time Pro versions and installers, so every one of
them is configurable. The defaults match the schema observed on a real
installation (`iclock_transaction`).
"""

from datetime import datetime

DEFAULTS = {
	"table": "iclock_transaction",
	"user_column": "emp_code",
	"time_column": "punch_time",
	"serial_column": "terminal_sn",
	"state_column": "punch_state",
}


class WDMSReader:
	driver_name = "WDMS"

	def __init__(self, device, logger):
		self.device = device
		self.log = logger
		self.settings = {**DEFAULTS, **(device.get("wdms") or {})}

	# ------------------------------------------------------------- plumbing

	def connect(self):
		"""Overridden in tests. Returns a DB-API connection."""
		try:
			import pyodbc
		except ImportError:
			raise RuntimeError(
				"pyodbc is not installed. It is needed only for WDMS/Time Pro sources. "
				"Install it with: pip install pyodbc"
			)
		dsn = self.device.get("connection_string")
		if not dsn:
			raise RuntimeError(
				"This device has no WDMS connection string. Set it in the agent config under "
				"`devices` for this serial, e.g. "
				"'DRIVER={ODBC Driver 17 for SQL Server};SERVER=.\\\\TIMEPRO;DATABASE=wdms;UID=..;PWD=..'"
			)
		return pyodbc.connect(dsn, timeout=30, readonly=True)

	# --------------------------------------------------------------- reader

	def query(self, since):
		s = self.settings
		where = []
		params = []
		if since:
			where.append(f"[{s['time_column']}] > ?")
			params.append(since)
		if self.device.get("serial_no"):
			where.append(f"[{s['serial_column']}] = ?")
			params.append(self.device["serial_no"])

		clause = ("WHERE " + " AND ".join(where)) if where else ""
		sql = (
			f"SELECT [{s['user_column']}], [{s['time_column']}], "
			f"[{s['serial_column']}], [{s['state_column']}] "
			f"FROM [{s['table']}] {clause} "
			f"ORDER BY [{s['time_column']}] ASC"
		)
		return sql, params

	def read(self, since: datetime | None = None):
		sql, params = self.query(since)
		connection = None
		try:
			connection = self.connect()
			cursor = connection.cursor()
			cursor.execute(sql, params)
			rows = cursor.fetchall()
		finally:
			if connection:
				try:
					connection.close()
				except Exception:
					pass

		punches = []
		for row in rows:
			device_user_id, punch_time, serial, state = row[0], row[1], row[2], row[3]
			if device_user_id is None or punch_time is None:
				continue
			if isinstance(punch_time, datetime):
				punch_time = punch_time.strftime("%Y-%m-%d %H:%M:%S")
			punches.append(
				{
					"device_serial": (serial or self.device.get("serial_no") or "").strip(),
					"device_user_id": str(device_user_id).strip(),
					"punch_time": str(punch_time),
					"raw_log_type": None if state is None else str(state),
					"payload": {"source": "wdms"},
				}
			)
		return punches
