"""Find out which eSSL / ZKTeco attendance database this is, read-only.

Run it once at commissioning, before the agent is configured. It connects with
the login you give it, looks for the punch table of every eSSL variant the agent
understands, and prints the `sources` block to put in the agent's config.json —
plus the reader serials to register in GroHR as Biometric Devices.

    python tools/essl_discover.py --engine mssql --host 192.168.1.20 --user grohr_ro
    python tools/essl_discover.py --engine mysql --host 192.168.1.20 --database biotime --user grohr_ro --json

It only ever lists databases/tables and SELECTs. The password comes from
--password, the ESSL_DB_PASSWORD environment variable, or a prompt (preferred:
it keeps it out of the shell history).

Variants understood
-------------------
  iclock_transaction   eSSL Time Pro / WDMS, Easy Time Pro, BioTime, ZKBioTime (SQL Server or MySQL)
  DeviceLogs_<m>_<y>   eSSL eTimeTrackLite / eTimeTrack (one table per month, SQL Server)
Anything else that looks like a punch table (CHECKINOUT, att_log, ...) is listed as
NOT SUPPORTED, so you know before promising a date.
"""

import argparse
import getpass
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grohr_agent.devices.sqldb import SQLReader, _list_tables  # noqa: E402

SYSTEM_DBS = {"master", "model", "msdb", "tempdb", "information_schema", "mysql", "performance_schema", "sys"}

ICLOCK = {
	"table": "iclock_transaction", "user_column": "emp_code", "time_column": "punch_time",
	"serial_column": "terminal_sn", "state_column": "punch_state",
}
ETIMETRACK = {
	"table_pattern": "DeviceLogs_{m}_{Y}", "user_column": "UserId", "time_column": "LogDate",
	"serial_column": "DeviceId", "state_column": "Direction",
}
MONTHLY = re.compile(r"^devicelogs_(\d{1,2})_(\d{4})$", re.I)
UNSUPPORTED_HINTS = ("checkinout", "att_log", "attlog", "attendance_log", "acc_transaction", "t_lg", "punchlog")


class _Log:
	def __getattr__(self, _name):
		return lambda msg: print(msg, file=sys.stderr)


def _reader(args, database, source=None):
	cfg = {"engine": args.engine, "host": args.host, "database": database, "user": args.user, "password": args.password}
	if args.port:
		cfg["port"] = args.port
	if args.odbc_driver:
		cfg["odbc_driver"] = args.odbc_driver
	if args.trusted:
		cfg["trusted_connection"] = True
	cfg.update(source or {})
	return SQLReader({"name": "discover", "source": cfg}, _Log())


def _databases(args):
	probe = _reader(args, "information_schema" if args.engine == "mysql" else "master")
	conn = probe.connect()
	try:
		cur = conn.cursor()
		cur.execute("SHOW DATABASES" if args.engine == "mysql" else "SELECT name FROM sys.databases")
		return [str(r[0]) for r in cur.fetchall() if str(r[0]).lower() not in SYSTEM_DBS]
	finally:
		conn.close()


def _columns(cur, reader, table):
	top = "TOP 1 " if reader.engine == "mssql" else ""
	tail = " LIMIT 1" if reader.engine == "mysql" else ""
	cur.execute(f"SELECT {top}* FROM {reader.q(table)}{tail}")
	cur.fetchall()
	return [d[0] for d in (cur.description or [])]


def _has(columns, source):
	low = {c.lower() for c in columns}
	needed = [source["user_column"], source["time_column"]]
	return all(n.lower() in low for n in needed)


def _scan_table(reader, cur, table, source):
	"""Row count, time range and the readers seen in one punch table."""
	t = reader.q(source["time_column"])
	cur.execute(f"SELECT COUNT(*), MIN({t}), MAX({t}) FROM {reader.q(table)}")
	count, first, last = cur.fetchall()[0]
	readers = []
	if source.get("serial_column"):
		sc = reader.q(source["serial_column"])
		cur.execute(f"SELECT {sc}, COUNT(*), MAX({t}) FROM {reader.q(table)} GROUP BY {sc}")
		readers = [(str(r[0]).strip(), int(r[1]), r[2]) for r in cur.fetchall() if r[0] is not None]
	return count, first, last, readers


def discover_database(args, database):
	"""-> dict describing the punch table found in `database`, plus unsupported lookalikes."""
	reader = _reader(args, database)
	conn = reader.connect()
	out = {"database": database, "variant": None, "unsupported": []}
	try:
		cur = conn.cursor()
		tables = _list_tables(cur, reader.engine) or []
		bare = {t.split(".")[-1].lower(): t.split(".")[-1] for t in tables}

		if "iclock_transaction" in bare:
			source = {**ICLOCK, "table": bare["iclock_transaction"]}
			cols = _columns(cur, reader, source["table"])
			out["columns"] = cols
			if _has(cols, source):
				count, first, last, readers = _scan_table(reader, cur, source["table"], source)
				out.update(variant="iclock_transaction", source=source, rows=count, first=first, latest=last,
				           readers=readers)
			else:
				out["unsupported"].append(f"iclock_transaction without columns emp_code/punch_time: {cols}")

		months = sorted(
			((int(m.group(2)), int(m.group(1)), name) for name, m in
			 ((bare[k], MONTHLY.match(bare[k])) for k in bare) if m)
		)
		if months and out["variant"] is None:
			source = dict(ETIMETRACK)
			# keep the real spelling of the table names for the pattern
			sample = months[-1][2]
			m = MONTHLY.match(sample)
			prefix = sample[: sample.lower().index("_")]
			source["table_pattern"] = f"{prefix}_{{m}}_{{Y}}"
			cols = _columns(cur, reader, sample)
			out["columns"] = cols
			low = {c.lower(): c for c in cols}
			for key in ("user_column", "time_column", "serial_column", "state_column"):
				if source[key].lower() in low:
					source[key] = low[source[key].lower()]
			if _has(cols, source):
				rows, first, last, readers = 0, None, None, {}
				for year, month, name in months[-3:]:
					count, f, l, rd = _scan_table(reader, cur, name, source)
					rows += count
					first = f if first is None or (f and f < first) else first
					last = l if last is None or (l and l > last) else last
					for serial, n, latest in rd:
						n0, l0 = readers.get(serial, (0, None))
						readers[serial] = (n0 + n, latest if l0 is None or (latest and latest > l0) else l0)
				out.update(variant="DeviceLogs_<m>_<yyyy>", source=source, rows=rows, first=first, latest=last,
				           readers=[(s, n, l) for s, (n, l) in readers.items()],
				           monthly_tables=[n for _, _, n in months])
			else:
				out["unsupported"].append(f"{sample} without UserId/LogDate columns: {cols}")

		for name in bare.values():
			if name.lower() in UNSUPPORTED_HINTS and out["variant"] is None:
				out["unsupported"].append(f"table {name} looks like a punch table of a variant this agent does not read")
	finally:
		conn.close()
	return out


def _db_time(args, database):
	"""The database server's own clock, to compare with this machine's."""
	reader = _reader(args, database)
	conn = reader.connect()
	try:
		cur = conn.cursor()
		cur.execute("SELECT NOW()" if reader.engine == "mysql" else "SELECT GETDATE()")
		return cur.fetchall()[0][0]
	except Exception:
		return None
	finally:
		conn.close()


def _fmt(value):
	return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else str(value)


def main():
	p = argparse.ArgumentParser(prog="essl_discover", description=__doc__.split("\n\n")[0])
	p.add_argument("--engine", required=True, choices=["mssql", "mysql"])
	p.add_argument("--host", required=True)
	p.add_argument("--port", type=int)
	p.add_argument("--database", help="omit to scan every database the login can see")
	p.add_argument("--user")
	p.add_argument("--password", default=os.environ.get("ESSL_DB_PASSWORD"))
	p.add_argument("--trusted", action="store_true", help="Windows authentication (SQL Server; Windows agent only)")
	p.add_argument("--odbc-driver", help='SQL Server only, default "ODBC Driver 17 for SQL Server"')
	p.add_argument("--active-days", type=int, default=90,
	               help="readers silent for longer than this are listed as stale (default 90)")
	p.add_argument("--json", action="store_true", help="machine-readable result on stdout (used by the installer)")
	args = p.parse_args()
	if not args.trusted and not args.user:
		p.error("--user is required (or --trusted)")
	if not args.trusted and args.password is None:
		args.password = getpass.getpass("Database password: ")

	say = (lambda *_a, **_k: None) if args.json else print
	try:
		databases = [args.database] if args.database else _databases(args)
	except Exception as exc:
		print(f"CANNOT CONNECT: {exc}", file=sys.stderr)
		return 2
	say(f"Databases to check: {', '.join(databases) or '(none visible to this login)'}")

	found, other = [], []
	for database in databases:
		try:
			info = discover_database(args, database)
		except Exception as exc:  # one unreadable database must not hide the rest
			say(f"  {database}: skipped ({str(exc).splitlines()[0][:120]})")
			continue
		(found if info["variant"] else other).append(info)
		if not info["variant"]:
			for note in info["unsupported"]:
				say(f"  {database}: NOT SUPPORTED - {note}")
			continue
		say(f"\n== {database}: {info['variant']}  rows {info['rows']}  {_fmt(info['first'])} .. {_fmt(info['latest'])}")
		cutoff = datetime.now() - timedelta(days=args.active_days)
		for serial, n, latest in sorted(info["readers"], key=lambda r: str(r[2]), reverse=True):
			stale = isinstance(latest, datetime) and latest < cutoff
			say(f"   reader {serial:<22} {n:>9} punches, latest {_fmt(latest)}{'   (STALE)' if stale else ''}")

	db_time = _db_time(args, found[0]["database"]) if found else None
	if db_time and not args.json:
		drift = (db_time - datetime.now()).total_seconds()
		say(f"\nDatabase server clock: {_fmt(db_time)}  ({drift:+.0f} s from this machine)"
		    + ("   <-- CHECK TIME SYNC: punches more than 5 min in the future are rejected" if drift > 240 else ""))

	if args.json:
		print(json.dumps({"db_time": _fmt(db_time) if db_time else None, "found": [
			{"database": f["database"], "variant": f["variant"], "source": f["source"], "rows": f["rows"],
			 "first": _fmt(f["first"]), "latest": _fmt(f["latest"]),
			 "readers": [{"serial": s, "punches": n, "latest": _fmt(l),
			              "stale": isinstance(l, datetime) and l < datetime.now() - timedelta(days=args.active_days)}
			             for s, n, l in f["readers"]]} for f in found],
			"unsupported": [{"database": o["database"], "notes": o["unsupported"]} for o in other if o["unsupported"]]}))
		return 0 if found else 3

	if not found:
		say("\nNo punch table of a supported eSSL variant was found. Send the output above to GroHR support.")
		return 3
	for f in found:
		src = {"engine": args.engine, "host": args.host, **({"port": args.port} if args.port else {}),
		       "database": f["database"], "user": args.user or "", "password": "<typed at install>", **f["source"]}
		say(f"\nconfig.json -> sources for database {f['database']}:")
		say(json.dumps({"*": src}, indent=2))
	return 0


if __name__ == "__main__":
	sys.exit(main())
