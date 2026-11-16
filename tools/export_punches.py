"""One-off: export punches from the attendance-software database to CSV.

Used for a backfill or an audit, never by the running agent. Read-only: it only
SELECTs, over the same connection settings the agent uses (`config.json` ->
`sources`), and it never talks to the GroHR server.

    python tools/export_punches.py --from 2026-07-01 --to 2026-09-30
    python tools/export_punches.py --from 2026-07-01 --to 2026-09-30 --source "*" --out C:\\GroHR\\export

Writes three files into --out:

  punches-<from>-<to>.csv    device_serial, device_user_id, punch_time, raw_log_type
  people.csv                 every row of the software's employee table, if it has one
                             (eSSL/BioTime: personnel_employee) — names for HR mapping
  summary-<from>-<to>.csv    punches per day per reader, to compare with GroHR

Upload the punches CSV to the server and run biometric_hub's backfill_csv tool.
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grohr_agent.devices.sqldb import SQLReader, _read_only_session  # noqa: E402

PEOPLE_TABLES = ("personnel_employee", "userinfo", "employees")
# Only identity columns leave the database — never passwords, card numbers or photos.
PEOPLE_COLUMNS = (
	"emp_code", "badgenumber", "userid", "first_name", "last_name", "name", "nickname",
	"department_id", "defaultdeptid", "position_id", "hire_date", "is_active", "status",
)


class _Log:
	def __getattr__(self, _name):
		return lambda msg: print(msg)


def main():
	p = argparse.ArgumentParser(prog="export_punches")
	p.add_argument("--config", default="config.json")
	p.add_argument("--source", default="*", help='key under "sources" in config.json (default "*")')
	p.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD, inclusive")
	p.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD, inclusive")
	p.add_argument("--out", default="export")
	a = p.parse_args()

	d_from = date.fromisoformat(a.date_from)
	d_to = date.fromisoformat(a.date_to)
	if d_to < d_from:
		sys.exit("--to is before --from")

	with open(a.config, encoding="utf-8") as fh:
		cfg = json.load(fh)
	source = (cfg.get("sources") or {}).get(a.source)
	if not isinstance(source, dict):
		sys.exit(f'config.json has no sources["{a.source}"]')

	reader = SQLReader({"name": "export", "source": source}, _Log())
	s = reader.settings
	os.makedirs(a.out, exist_ok=True)

	conn = reader.connect()
	try:
		cur = conn.cursor()
		if reader.engine == "mysql":
			_read_only_session(cur)

		cols = [reader.q(s["user_column"]), reader.q(s["time_column"])]
		cols.append(reader.q(s["serial_column"]) if s.get("serial_column") else "NULL")
		cols.append(reader.q(s["state_column"]) if s.get("state_column") else "NULL")
		t = reader.q(s["time_column"])
		sql = (
			f"SELECT {', '.join(cols)} FROM {reader.q(s['table'])} "
			f"WHERE {t} >= {reader.mark} AND {t} < {reader.mark} ORDER BY {t} ASC"
		)
		start = datetime.combine(d_from, datetime.min.time())
		end = datetime.combine(d_to + timedelta(days=1), datetime.min.time())
		cur.execute(sql, [start, end])

		path = os.path.join(a.out, f"punches-{d_from}-{d_to}.csv")
		per_day, seen, written, skipped = Counter(), set(), 0, 0
		with open(path, "w", newline="", encoding="utf-8") as fh:
			w = csv.writer(fh)
			w.writerow(["device_serial", "device_user_id", "punch_time", "raw_log_type"])
			while True:
				rows = cur.fetchmany(5000)
				if not rows:
					break
				for user, ptime, serial, state in rows:
					if user is None or ptime is None or str(user).strip() == "":
						skipped += 1
						continue
					ptime = ptime.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ptime, datetime) else str(ptime)[:19]
					serial = "" if serial is None else str(serial).strip()
					key = (serial, str(user).strip(), ptime)
					if key in seen:
						skipped += 1
						continue
					seen.add(key)
					w.writerow([serial, str(user).strip(), ptime, "" if state is None else str(state).strip()])
					per_day[(ptime[:10], serial)] += 1
					written += 1
		print(f"Punches: {written} written to {path} ({skipped} blank/duplicate rows skipped)")

		spath = os.path.join(a.out, f"summary-{d_from}-{d_to}.csv")
		with open(spath, "w", newline="", encoding="utf-8") as fh:
			w = csv.writer(fh)
			w.writerow(["date", "device_serial", "punches"])
			for (day, serial), n in sorted(per_day.items()):
				w.writerow([day, serial, n])
		print(f"Summary: {spath}")

		for table in PEOPLE_TABLES:
			try:
				cur.execute(f"SELECT * FROM {reader.q(table)} WHERE 1=0")
				cur.fetchall()
			except Exception:
				continue
			names = [d[0] for d in cur.description or [] if d[0].lower() in PEOPLE_COLUMNS]
			if not names:
				continue
			cur.execute(f"SELECT {', '.join(reader.q(n) for n in names)} FROM {reader.q(table)}")
			ppath = os.path.join(a.out, "people.csv")
			with open(ppath, "w", newline="", encoding="utf-8") as fh:
				w = csv.writer(fh)
				w.writerow(names)
				n = 0
				for row in cur.fetchall():
					w.writerow(["" if v is None else v for v in row])
					n += 1
			print(f"People: {n} rows from {table} written to {ppath}")
			break
		else:
			print("People: no employee table found (looked for " + ", ".join(PEOPLE_TABLES) + ")")
	finally:
		conn.close()


if __name__ == "__main__":
	main()
