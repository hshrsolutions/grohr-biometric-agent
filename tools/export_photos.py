"""One-off: export employee profile photos from the attendance software.

So HR can see a face next to every machine user ID and confirm who it is in
GroHR. Read-only: it only SELECTs (same connection settings as the agent,
`config.json` -> `sources`) and copies image files; it never writes to the
attendance software and never talks to the GroHR server.

    python tools/export_photos.py --probe
    python tools/export_photos.py --photo-root "C:\\BioTime" --out C:\\GroHR\\export

--probe   lists every table/column whose name looks like a photo, how many rows
          have one, and a few sample values. Run this first: where the photo
          lives differs between Easy Time Pro / BioTime versions.

The export writes `photos-<date>.zip`:
  photos/<device user id>.<jpg|png>
  manifest.csv   device_user_id, name, photo_file, source
Upload the zip and run biometric_hub's import_device_users tool on the server.

Photos are personal data. Keep the zip on the office PC or hand it over
directly — do not e-mail it around.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grohr_agent.devices.sqldb import SQLReader, _read_only_session  # noqa: E402
from grohr_agent.photos import _kind, to_image  # noqa: E402

PHOTO_NAME = re.compile(r"photo|image|img|pic|avatar|portrait", re.I)
# eSSL / BioTime first, then older ZK software.
DEFAULT_TABLE, DEFAULT_ID, DEFAULT_NAME, DEFAULT_PHOTO = "personnel_employee", "emp_code", "first_name", "photo"


class _Log:
	def __getattr__(self, _name):
		return lambda msg: print(msg)


def probe(cur, reader):
	"""Every column that looks like it holds a photo."""
	if reader.engine == "mssql":
		cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS")
	else:
		cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")
	hits = [(t, c) for t, c in cur.fetchall() if PHOTO_NAME.search(c) or PHOTO_NAME.search(t)]
	if not hits:
		print("No photo-like columns found.")
		return
	for table, col in hits:
		try:
			cur.execute(f"SELECT COUNT(*) FROM {reader.q(table)} WHERE {reader.q(col)} IS NOT NULL")
			n = cur.fetchone()[0]
			top = "TOP 3 " if reader.engine == "mssql" else ""
			lim = "" if reader.engine == "mssql" else " LIMIT 3"
			cur.execute(f"SELECT {top}{reader.q(col)} FROM {reader.q(table)} WHERE {reader.q(col)} IS NOT NULL{lim}")
			samples = []
			for (v,) in cur.fetchall():
				if isinstance(v, (bytes, bytearray, memoryview)):
					samples.append(f"<{len(bytes(v))} bytes, {_kind(bytes(v)) or 'not an image'}>")
				else:
					s = str(v)
					samples.append(s[:80] + ("..." if len(s) > 80 else ""))
			print(f"{table}.{col}: {n} rows with a value  e.g. {samples}")
		except Exception as e:
			print(f"{table}.{col}: could not read ({type(e).__name__})")


def main():
	p = argparse.ArgumentParser(prog="export_photos")
	p.add_argument("--config", default="config.json")
	p.add_argument("--source", default="*", help='key under "sources" in config.json (default "*")')
	p.add_argument("--probe", action="store_true", help="only list where photos might be")
	p.add_argument("--table", default=DEFAULT_TABLE)
	p.add_argument("--id-column", default=DEFAULT_ID)
	p.add_argument("--name-column", default=DEFAULT_NAME)
	p.add_argument("--photo-column", default=DEFAULT_PHOTO)
	p.add_argument("--photo-root", action="append", default=[],
	               help="install folder of the attendance software (repeatable); file paths are looked up under it")
	p.add_argument("--out", default="export")
	a = p.parse_args()

	with open(a.config, encoding="utf-8") as fh:
		cfg = json.load(fh)
	source = (cfg.get("sources") or {}).get(a.source)
	if not isinstance(source, dict):
		sys.exit(f'config.json has no sources["{a.source}"]')

	reader = SQLReader({"name": "photos", "source": source}, _Log())
	conn = reader.connect()
	try:
		cur = conn.cursor()
		if reader.engine == "mysql":
			_read_only_session(cur)
		if a.probe:
			probe(cur, reader)
			return

		cols = ", ".join(reader.q(c) for c in (a.id_column, a.name_column, a.photo_column))
		cur.execute(f"SELECT {cols} FROM {reader.q(a.table)}")
		rows = cur.fetchall()
	finally:
		conn.close()

	os.makedirs(a.out, exist_ok=True)
	zpath = os.path.join(a.out, f"photos-{date.today()}.zip")
	found, missing = 0, []
	manifest = io.StringIO()
	w = csv.writer(manifest)
	w.writerow(["device_user_id", "name", "photo_file", "source"])
	with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
		for uid, name, value in rows:
			uid = str(uid or "").strip()
			if not uid:
				continue
			data, ext, src = to_image(value, a.photo_root)
			fname = ""
			if data:
				fname = f"photos/{re.sub(r'[^A-Za-z0-9_.-]', '_', uid)}.{ext}"
				z.writestr(fname, data)
				found += 1
			elif value not in (None, ""):
				missing.append((uid, src))
			w.writerow([uid, name or "", fname, src])
		z.writestr("manifest.csv", manifest.getvalue())

	print(f"{len(rows)} users, {found} photos exported -> {zpath}")
	if missing:
		print(f"{len(missing)} users have a photo value that could not be read, e.g.:")
		for uid, why in missing[:10]:
			print(f"  {uid}: {why}")
		print("If these are 'file not found', pass --photo-root <attendance software install folder>.")


if __name__ == "__main__":
	main()
