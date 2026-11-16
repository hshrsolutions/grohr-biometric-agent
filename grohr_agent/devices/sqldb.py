"""Attendance software databases — MySQL / MariaDB and SQL Server.

At many sites the readers talk only to the vendor's attendance software
(eSSL Time Pro / WDMS, eTimeTrack, BioTime, ZKTime and friends), which writes
every punch into a database on a Windows machine in the office. The punches are
there; the device is not on offer. So this reader queries that database.

**It is strictly read-only.** It issues SELECTs and writes nothing — no status
column, no flag, no schema change. On MySQL the session is also put into READ
ONLY mode, so the database itself refuses a write even if one were attempted;
on SQL Server the ODBC connection is opened read-only. Give it a database login
that holds SELECT on the one table, and nothing else.

**The credentials stay on the premises.** The server's device list says *which*
device to read; *how* to reach the database (host, login, password, table and
column names) lives only in the agent's own `config.json` under `sources`. The
database password never leaves the customer's network.

Reading window
--------------
The cursor is the server's (`last_punch_time`), never a file here. Attendance
software often receives punches late — a reader that was offline uploads its
backlog hours later with the original times — so each read starts
`lookback_minutes` *before* the cursor. The spool's sent-ledger makes that
re-read free: a punch already delivered is recognised locally and not sent
again. On a first run with no cursor the read starts at the device's
`ignore_punches_before`, or `initial_days` ago, never at the beginning of time.

Rows are read in pages of `max_rows`, oldest first, so a long backlog is worked
through in one cycle without holding a year of history in memory.

Monthly tables
--------------
eSSL eTimeTrackLite keeps punches in one table per month (`DeviceLogs_9_2026`).
Set `table_pattern` instead of `table` and the reader reads every month from the
start of the window to now, oldest first, skipping months that have no table:

    "table_pattern": "DeviceLogs_{m}_{Y}"      {m} month 1-12, {mm} month 01-12, {Y} year
"""

from datetime import datetime, timedelta

ENGINES = ("mysql", "mssql")

# Column defaults match the eSSL / ZKTeco `iclock_transaction` schema, which is
# what Time Pro (WDMS), BioTime and most ZK-based software use.
DEFAULTS = {
	"engine": "mysql",
	"table": "iclock_transaction",
	"user_column": "emp_code",
	"time_column": "punch_time",
	"serial_column": "terminal_sn",
	"state_column": "punch_state",
	"lookback_minutes": 1440,
	"initial_days": 7,
	"max_rows": 5000,
	"max_pages": 50,
}


class SQLReader:
	driver_name = "SQL Database"
	default_engine = "mysql"

	def __init__(self, device, logger):
		self.device = device
		self.log = logger
		# `wdms` is the key the first release used; kept so old configs still work.
		source = {**(device.get("wdms") or {}), **(device.get("source") or {})}
		self.settings = {**DEFAULTS, "engine": self.default_engine, **source}
		engine = str(self.settings["engine"]).lower()
		if engine in ("mariadb",):
			engine = "mysql"
		if engine in ("sqlserver", "mssql", "sql server"):
			engine = "mssql"
		if engine not in ENGINES:
			raise ValueError(f"Unknown database engine {self.settings['engine']!r}; use 'mysql' or 'mssql'.")
		self.settings["engine"] = engine
		self.engine = engine

	# ------------------------------------------------------------- plumbing

	def connect(self):
		"""Returns a DB-API connection. Overridden in tests."""
		return _connect_mysql(self.settings) if self.engine == "mysql" else _connect_mssql(self.settings, self.device)

	def q(self, name):
		"""Quote an identifier for this engine. Identifiers come from the local
		config file, never from the network, but they are still checked."""
		if not name or any(c in str(name) for c in "`[]\";\n\r"):
			raise ValueError(f"Invalid column or table name {name!r} in the source config.")
		if self.engine == "mysql":
			return ".".join(f"`{part}`" for part in str(name).split("."))
		return ".".join(f"[{part}]" for part in str(name).split("."))

	@property
	def mark(self):
		return "%s" if self.engine == "mysql" else "?"

	# --------------------------------------------------------------- query

	def tables_for(self, start, now=None):
		"""Tables to read for a window beginning at `start`, oldest first.
		One table, unless `table_pattern` names monthly tables."""
		pattern = self.settings.get("table_pattern")
		if not pattern:
			return [self.settings["table"]]
		now = now or datetime.now()
		year, month = start.year, start.month
		names = []
		while (year, month) <= (now.year, now.month):
			names.append(
				pattern.replace("{mm}", f"{month:02d}").replace("{m}", str(month)).replace("{Y}", str(year))
			)
			month += 1
			if month > 12:
				year, month = year + 1, 1
		return names

	def query(self, since, limit=None, table=None):
		s = self.settings
		table = table or s["table"]
		columns = [self.q(s["user_column"]), self.q(s["time_column"])]
		columns.append(self.q(s["serial_column"]) if s.get("serial_column") else "NULL")
		columns.append(self.q(s["state_column"]) if s.get("state_column") else "NULL")

		where, params = [], []
		if since:
			# >=, not >: a second punch in the same second as the cursor must not be
			# lost. The overlap is absorbed by the sent-ledger and the server key.
			where.append(f"{self.q(s['time_column'])} >= {self.mark}")
			params.append(since)
		if s.get("serial_column") and self.device.get("serial_no") and s.get("filter_by_serial", True):
			where.append(f"{self.q(s['serial_column'])} = {self.mark}")
			params.append(self.device["serial_no"])

		clause = (" WHERE " + " AND ".join(where)) if where else ""
		top = f"TOP {int(limit)} " if (limit and self.engine == "mssql") else ""
		tail = f" LIMIT {int(limit)}" if (limit and self.engine == "mysql") else ""
		sql = (
			f"SELECT {top}{', '.join(columns)} FROM {self.q(table)}{clause} "
			f"ORDER BY {self.q(s['time_column'])} ASC{tail}"
		)
		return sql, params

	def window_start(self, since):
		"""Where this cycle's read begins. See the module docstring."""
		s = self.settings
		if since:
			return since - timedelta(minutes=int(s["lookback_minutes"] or 0))
		floor = _parse(self.device.get("ignore_punches_before"))
		if floor:
			return floor
		return datetime.now().replace(microsecond=0) - timedelta(days=int(s["initial_days"] or 7))

	# ---------------------------------------------------------------- read

	def read(self, since: datetime | None = None):
		start = self.window_start(since)
		limit = int(self.settings["max_rows"] or 5000)
		rows_all = []
		connection = None
		try:
			connection = self.connect()
			cursor = connection.cursor()
			if self.engine == "mysql":
				_read_only_session(cursor)
			tables = self.tables_for(start)
			if self.settings.get("table_pattern"):
				existing = _list_tables(cursor, self.engine)
				if existing is not None:
					have = {t.split(".")[-1].lower() for t in existing}
					tables = [t for t in tables if t.split(".")[-1].lower() in have]
			for table in tables:
				self._read_table(cursor, table, start, limit, rows_all)
		finally:
			if connection:
				try:
					connection.close()
				except Exception:
					pass

		return self.to_punches(rows_all)

	def _read_table(self, cursor, table, start, limit, rows_all):
		cursor_time = start
		for _ in range(int(self.settings["max_pages"] or 50)):
			sql, params = self.query(cursor_time, limit, table)
			cursor.execute(sql, params)
			rows = list(cursor.fetchall())
			rows_all.extend(rows)
			if len(rows) < limit:
				return
			last = rows[-1][1]
			if not isinstance(last, datetime) or (cursor_time and last <= cursor_time):
				# A full page all in one second — cannot page past it safely.
				self.log.warning(f"{self.device.get('name')}: {limit} rows share one timestamp; raise max_rows.")
				return
			cursor_time = last
		self.log.warning(
			f"{self.device.get('name')}: stopped after {self.settings['max_pages']} pages of {table}; "
			"the rest will be read next cycle."
		)

	def to_punches(self, rows):
		punches, seen = [], set()
		for row in rows:
			device_user_id, punch_time, serial, state = row[0], row[1], row[2], row[3]
			if device_user_id is None or punch_time is None or str(device_user_id).strip() == "":
				continue
			if isinstance(punch_time, datetime):
				punch_time = punch_time.strftime("%Y-%m-%d %H:%M:%S")
			else:
				punch_time = str(punch_time)[:19]
			serial = (str(serial).strip() if serial is not None else "") or (self.device.get("serial_no") or "").strip()
			key = (serial, str(device_user_id).strip(), punch_time)
			if key in seen:  # page overlap
				continue
			seen.add(key)
			punches.append(
				{
					"device_serial": serial,
					"device_user_id": str(device_user_id).strip(),
					"punch_time": punch_time,
					"raw_log_type": None if state is None else str(state).strip(),
					"payload": {"source": f"sql:{self.engine}"},
				}
			)
		return punches

	# --------------------------------------------------------------- probe

	def probe(self, out=print):
		"""Commissioning report: can we connect, is the table there, what does it
		hold. Read-only, needs no server. Used by `--probe`."""
		s = self.settings
		connection = self.connect()
		try:
			cursor = connection.cursor()
			if self.engine == "mysql":
				_read_only_session(cursor)
			out(f"  Connected ({self.engine}).")
			tables = _list_tables(cursor, self.engine)
			have = {t.split(".")[-1].lower() for t in tables} if tables is not None else None
			table = s["table"]
			if s.get("table_pattern"):
				recent = self.tables_for(datetime.now() - timedelta(days=100))
				found = [t for t in recent if have is None or t.split(".")[-1].lower() in have]
				if not found:
					out(f"  NO TABLE MATCHING {s['table_pattern']!r} for the last months. Tables in this database:")
					for name in (tables or [])[:200]:
						out(f"    {name}")
					return False
				table = found[-1]
				out(f"  Monthly tables in the last 100 days: {', '.join(found)} (checking {table})")
			elif have is not None and table.split(".")[-1].lower() not in have:
				out(f"  TABLE {table!r} NOT FOUND. Tables in this database:")
				for name in tables[:200]:
					out(f"    {name}")
				return False

			top = "TOP 1 " if self.engine == "mssql" else ""
			tail = " LIMIT 1" if self.engine == "mysql" else ""
			cursor.execute(f"SELECT {top}* FROM {self.q(table)}{tail}")
			cursor.fetchall()
			names = [d[0] for d in (cursor.description or [])]
			out(f"  Columns: {', '.join(names)}")
			missing = [
				s[k] for k in ("user_column", "time_column", "serial_column", "state_column")
				if s.get(k) and s[k].lower() not in [n.lower() for n in names]
			]
			if missing:
				out(f"  MISSING COLUMNS {missing} — set them under this source in config.json.")
				return False

			t = self.q(s["time_column"])
			cursor.execute(f"SELECT COUNT(*), MIN({t}), MAX({t}) FROM {self.q(table)}")
			count, first, last = cursor.fetchall()[0]
			out(f"  Rows: {count}   first punch: {first}   latest punch: {last}")

			if s.get("serial_column"):
				sc = self.q(s["serial_column"])
				cursor.execute(
					f"SELECT {sc}, COUNT(*), MAX({t}) FROM {self.q(table)} GROUP BY {sc} ORDER BY 3 DESC"
				)
				out("  Readers (serial, punches, latest) — register each serial as a Biometric Device:")
				for serial, n, latest in cursor.fetchall()[:100]:
					out(f"    {serial!s:<24} {n:>9}   {latest}")

			sql, params = self.query(None, 5, table)
			sql = sql.replace("ASC", "DESC")
			cursor.execute(sql, params)
			out("  Latest punches (user, time, serial, state):")
			for row in cursor.fetchall():
				out(f"    {row[0]!s:<12} {row[1]!s:<20} {row[2]!s:<20} {row[3]!s}")
			return True
		finally:
			try:
				connection.close()
			except Exception:
				pass


# ------------------------------------------------------------ connections


def _connect_mysql(s):
	try:
		import pymysql
	except ImportError:
		raise RuntimeError(
			"PyMySQL is not installed. It is needed for MySQL/MariaDB sources: pip install pymysql"
		)
	for key in ("host", "user", "database"):
		if not s.get(key):
			raise RuntimeError(f"The MySQL source has no `{key}`. Set it under `sources` in config.json.")
	return pymysql.connect(
		host=s["host"],
		port=int(s.get("port") or 3306),
		user=s["user"],
		password=s.get("password") or "",
		database=s["database"],
		charset=s.get("charset") or "utf8mb4",
		connect_timeout=30,
		read_timeout=300,
		autocommit=True,
	)


def _connect_mssql(s, device):
	try:
		import pyodbc
	except ImportError:
		raise RuntimeError(
			"pyodbc is not installed. It is needed for SQL Server sources: pip install pyodbc"
		)
	dsn = s.get("connection_string") or device.get("connection_string")
	if not dsn:
		if not (s.get("host") and s.get("database")):
			raise RuntimeError(
				"The SQL Server source needs either `connection_string`, or `host` and `database` "
				"(plus `user`/`password`, or `trusted_connection: true`)."
			)
		parts = [
			f"DRIVER={{{s.get('odbc_driver') or 'ODBC Driver 17 for SQL Server'}}}",
			f"SERVER={s['host']}" + (f",{s['port']}" if s.get("port") else ""),
			f"DATABASE={s['database']}",
		]
		if s.get("trusted_connection"):
			parts.append("Trusted_Connection=yes")
		else:
			parts += [f"UID={s.get('user') or ''}", f"PWD={s.get('password') or ''}"]
		if s.get("trust_server_certificate", True):
			parts.append("TrustServerCertificate=yes")
		dsn = ";".join(parts)
	return pyodbc.connect(dsn, timeout=30, readonly=True)


def _read_only_session(cursor):
	"""Belt and braces: the database itself will now refuse any write."""
	try:
		cursor.execute("SET SESSION TRANSACTION READ ONLY")
	except Exception:
		pass  # very old MySQL; the reader still only ever SELECTs


def _list_tables(cursor, engine):
	try:
		if engine == "mysql":
			cursor.execute("SHOW TABLES")
		else:
			cursor.execute("SELECT TABLE_SCHEMA + '.' + TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
		return [str(r[0]) for r in cursor.fetchall()]
	except Exception:
		return None


def _parse(value):
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	text = str(value)
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
		try:
			return datetime.strptime(text, fmt)
		except ValueError:
			continue
	return None
