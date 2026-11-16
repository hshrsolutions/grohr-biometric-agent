"""Answering the server's profile requests: name and photo for a few device
user IDs, read from the attendance software's employee table.

The server cannot call this PC; it lists the IDs it wants in the heartbeat
reply and we post the answers back. Read-only: one SELECT per batch, bound
parameters, a read-only session where the database supports it.

config.json (all optional; defaults are eSSL Easy Time Pro / BioTime):

    "profile": {"source": "*", "table": "personnel_employee", "id_column": "emp_code",
                "name_column": "first_name", "photo_column": "photo",
                "photo_roots": ["C:\\\\BioTime"]}
"""

import base64

from grohr_agent.devices.sqldb import SQLReader, _read_only_session
from grohr_agent.photos import to_image

DEFAULTS = {"table": "personnel_employee", "id_column": "emp_code", "name_column": "first_name",
            "photo_column": "photo"}
MAX_PHOTO = 2 * 1024 * 1024  # the server refuses anything bigger


def lookup(config, ids, log):
	"""[{device_user_id, found, name, photo_base64, photo_ext, error}] for `ids`."""
	settings = {**DEFAULTS, **(config.get("profile") or {})}
	sources = config.get("sources") or {}
	source = sources.get(settings.get("source") or "*") or next(
		(v for v in sources.values() if isinstance(v, dict)), None)
	if not source:
		return [{"device_user_id": i, "found": False, "error": "No database source in config.json"} for i in ids]

	reader = SQLReader({"name": "profiles", "source": source}, log)
	q = reader.q
	marks = ", ".join([reader.mark] * len(ids))
	sql = (f"SELECT {q(settings['id_column'])}, {q(settings['name_column'])}, {q(settings['photo_column'])} "
	       f"FROM {q(settings['table'])} WHERE {q(settings['id_column'])} IN ({marks})")
	conn = reader.connect()
	try:
		cur = conn.cursor()
		if reader.engine == "mysql":
			_read_only_session(cur)
		cur.execute(sql, list(ids))
		rows = {str(r[0]).strip(): r for r in cur.fetchall()}
	finally:
		conn.close()

	out = []
	roots = settings.get("photo_roots") or []
	for uid in ids:
		row = rows.get(str(uid).strip())
		if not row:
			out.append({"device_user_id": uid, "found": False})
			continue
		item = {"device_user_id": uid, "found": True, "name": row[1]}
		data, ext, why = to_image(row[2], roots)
		if data and len(data) <= MAX_PHOTO:
			item["photo_base64"], item["photo_ext"] = base64.b64encode(data).decode(), ext
		elif row[2] not in (None, ""):
			log.warning(f"Profile {uid}: photo not usable ({why}).")
		out.append(item)
	return out
