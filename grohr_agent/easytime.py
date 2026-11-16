"""Writing employees into eSSL Easy Time Pro (ZKTeco BioTime) through its own
REST API, so the software sends them to the machines in their area itself.

Never through the database: a row inserted straight into `personnel_employee`
is not sent to any machine — only users made through the app or the API are.
And never directly to a reader: the software would not know about the user and
would remove them on its next sync.

The server decides *what* to push (who, which area, whether to overwrite); this
module only translates one request into API calls and reports what happened.
It never overwrites a user it finds under the same code unless the request says
`overwrite`, so a shared or mistyped ID comes back as `Exists` for HR to look at.

config.json:

    "easytime": {
        "url": "http://127.0.0.1:8081",
        "username": "grohr_sync",
        "password": "…",
        "auth": "jwt",              # or "token" (older BioTime builds)
        "verify_tls": false,
        "dry_run": true,            # look things up, change nothing (commissioning)
        "paths": {...}              # only if this build's URLs differ
    }
"""

import requests

PATHS = {
	"jwt": "/jwt-api-token-auth/",
	"token": "/api-token-auth/",
	"employees": "/personnel/api/employees/",
	"departments": "/personnel/api/departments/",
	"areas": "/personnel/api/areas/",
	"resigns": "/personnel/api/resigns/",
}
DEFAULTS = {"auth": "jwt", "verify_tls": False, "timeout_seconds": 30, "dry_run": False}
MAX_PER_CYCLE = 50


class EasyTimeError(Exception):
	pass


class EasyTime:
	def __init__(self, settings, log=None):
		self.s = {**DEFAULTS, **(settings or {})}
		if not self.s.get("url"):
			raise EasyTimeError("config.json has no easytime.url")
		self.url = self.s["url"].rstrip("/")
		self.paths = {**PATHS, **(self.s.get("paths") or {})}
		self.log = log
		self.http = requests.Session()
		self.http.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
		self._ids = {"departments": {}, "areas": {}}
		self._logged_in = False

	@property
	def dry_run(self):
		return bool(self.s.get("dry_run"))

	# ------------------------------------------------------------------ http

	def _req(self, method, path, **kw):
		try:
			r = self.http.request(method, self.url + path, timeout=self.s["timeout_seconds"],
			                      verify=self.s["verify_tls"], **kw)
		except requests.RequestException as e:
			raise EasyTimeError(f"cannot reach Easy Time Pro at {self.url}: {type(e).__name__}: {e}")
		if r.status_code in (401, 403) and self._logged_in:
			raise EasyTimeError(f"HTTP {r.status_code}: the API user lacks permission for {path}")
		if r.status_code >= 400:
			raise EasyTimeError(f"HTTP {r.status_code} on {method} {path}: {r.text[:300]}")
		try:
			return r.json() if r.content else {}
		except ValueError:
			raise EasyTimeError(f"{method} {path} did not return JSON — is {self.url} really Easy Time Pro / BioTime?")

	def login(self):
		if self._logged_in:
			return
		kind = "token" if self.s["auth"] == "token" else "jwt"
		body = self._req("POST", self.paths[kind], json={"username": self.s.get("username"),
		                                                 "password": self.s.get("password")})
		token = body.get("token") if isinstance(body, dict) else None
		if not token:
			raise EasyTimeError("login returned no token — check easytime.username / password / auth")
		self.http.headers["Authorization"] = f"{'Token' if kind == 'token' else 'JWT'} {token}"
		self._logged_in = True

	def _list(self, path, **params):
		body = self._req("GET", path, params=params)
		if isinstance(body, list):
			return body
		return body.get("data") or body.get("results") or []

	# ---------------------------------------------------------------- lookups

	def find_employee(self, emp_code):
		"""Exact match only: the API filter may be a 'contains'."""
		rows = self._list(self.paths["employees"], emp_code=emp_code, page_size=50)
		return next((r for r in rows if str(r.get("emp_code", "")).strip() == str(emp_code).strip()), None)

	def _id_for(self, kind, code):
		code = str(code).strip()
		cache = self._ids[kind]
		if code not in cache:
			field = "dept_code" if kind == "departments" else "area_code"
			rows = self._list(self.paths[kind], **{field: code, "page_size": 50})
			hit = next((r for r in rows if str(r.get(field, "")).strip() == code), None)
			cache[code] = hit.get("id") if hit else None
		if cache[code] is None:
			label = "Department" if kind == "departments" else "Area"
			raise EasyTimeError(f"{label} code {code!r} does not exist in Easy Time Pro")
		return cache[code]

	def departments(self):
		return self._list(self.paths["departments"], page_size=500)

	def areas(self):
		return self._list(self.paths["areas"], page_size=500)

	# ----------------------------------------------------------------- writes

	def apply(self, push):
		"""One request from the server -> one result for it. Never raises."""
		result = {"sync_id": push.get("sync_id"), "emp_code": push.get("emp_code")}
		try:
			self.login()
			action = push.get("action")
			if action == "upsert":
				result.update(self._upsert(push))
			elif action == "disable":
				result.update(self._disable(push))
			else:
				result.update(outcome="Error", message=f"unknown action {action!r}")
		except EasyTimeError as e:
			result.update(outcome="Error", message=str(e)[:500])
		except Exception as e:
			result.update(outcome="Error", message=f"{type(e).__name__}: {e}"[:500])
		return result

	def _upsert(self, p):
		existing = self.find_employee(p["emp_code"])
		body = {
			"emp_code": str(p["emp_code"]),
			"first_name": (p.get("first_name") or "")[:25],
			"last_name": (p.get("last_name") or "")[:25],
			"department": self._id_for("departments", p.get("department_code") or "1"),
			"area": [self._id_for("areas", c) for c in p.get("area_codes") or []],
		}
		if not body["area"]:
			return {"outcome": "Error", "message": "no area to put this employee in"}
		for key in ("card_no", "mobile", "hire_date", "gender", "email"):
			if p.get(key):
				body[key] = p[key]

		if existing and not p.get("overwrite"):
			return {"outcome": "Exists", "remote_id": existing.get("id"),
			        "existing_name": _full_name(existing), "message": "already in Easy Time Pro; not changed"}
		if self.dry_run:
			verb = "update" if existing else "create"
			return {"outcome": "Dry Run", "remote_id": existing.get("id") if existing else None,
			        "existing_name": _full_name(existing) if existing else None,
			        "message": f"would {verb} {body['emp_code']} {body['first_name']} in {len(body['area'])} area(s)"}
		if existing:
			body.pop("emp_code")
			self._req("PATCH", f"{self.paths['employees']}{existing['id']}/", json=body)
			return {"outcome": "Updated", "remote_id": existing["id"]}
		made = self._req("POST", self.paths["employees"], json=body)
		return {"outcome": "Created", "remote_id": (made or {}).get("id")}

	def _disable(self, p):
		existing = self.find_employee(p["emp_code"])
		if not existing:
			return {"outcome": "Not Found", "message": "not in Easy Time Pro; nothing to remove"}
		if self.dry_run:
			return {"outcome": "Dry Run", "remote_id": existing.get("id"), "existing_name": _full_name(existing),
			        "message": f"would resign {p['emp_code']} and remove them from the machines"}
		self._req("POST", self.paths["resigns"], json={
			"employee": existing["id"],
			"resign_date": p.get("resign_date"),
			"resign_type": 1,
			"disableatt": True,
			"reason": (p.get("reason") or "Left - from GroHR")[:100],
		})
		return {"outcome": "Resigned", "remote_id": existing["id"]}


def _full_name(row):
	return " ".join(str(row.get(k) or "").strip() for k in ("first_name", "last_name")).strip()


def run(settings, pushes, log):
	"""All of one heartbeat's requests through one login."""
	try:
		api = EasyTime(settings, log)
	except EasyTimeError as e:
		return [{"sync_id": p.get("sync_id"), "outcome": "Error", "message": str(e)} for p in pushes]
	return [api.apply(p) for p in pushes[:MAX_PER_CYCLE]]


def probe(settings):
	"""--probe-easytime: log in and list departments and areas. Changes nothing."""
	api = EasyTime(settings)
	print(f"Easy Time Pro at {api.url} ({api.s['auth']} login as {api.s.get('username')!r})")
	api.login()
	print("  login OK")
	depts, areas = api.departments(), api.areas()
	print(f"  {len(depts)} department(s):")
	for d in depts[:100]:
		print(f"    code {d.get('dept_code')!s:>8}  {d.get('dept_name')}")
	print(f"  {len(areas)} area(s) — map GroHR branches to these codes in Biometric Settings:")
	for a in areas[:100]:
		print(f"    code {a.get('area_code')!s:>8}  {a.get('area_name')}")
	print(f"  dry_run is {'ON — nothing will be written' if api.dry_run else 'OFF — requests will be applied'}")
	return 0
