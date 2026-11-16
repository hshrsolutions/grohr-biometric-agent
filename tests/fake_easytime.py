"""An in-memory stand-in for the Easy Time Pro / BioTime REST API — just the
calls the agent makes, with the same shapes (`{"count", "data": [...]}` lists,
JWT login, ids not codes on write). For tests only; never packed.

	srv = FakeEasyTime().start()   # srv.url, srv.employees, srv.calls
	...
	srv.stop()
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "fake-jwt-token"


class FakeEasyTime:
	def __init__(self, username="api", password="secret"):
		self.username, self.password = username, password
		self.departments = [{"id": 1, "dept_code": "1", "dept_name": "Department"},
		                    {"id": 7, "dept_code": "HR", "dept_name": "Human Resources"}]
		self.areas = [{"id": 2, "area_code": "MDU", "area_name": "Madurai"},
		              {"id": 3, "area_code": "VNR", "area_name": "Virudhunagar"}]
		self.employees = []
		self.resigns = []
		self.calls = []  # (method, path) of every write
		self._next = 100

	# -------------------------------------------------------------- lifecycle

	def start(self):
		fake = self

		class Handler(BaseHTTPRequestHandler):
			def log_message(self, *a):
				pass

			def _send(self, code, body):
				data = json.dumps(body).encode()
				self.send_response(code)
				self.send_header("Content-Type", "application/json")
				self.send_header("Content-Length", str(len(data)))
				self.end_headers()
				self.wfile.write(data)

			def _body(self):
				n = int(self.headers.get("Content-Length") or 0)
				return json.loads(self.rfile.read(n) or b"{}")

			def _authed(self):
				return self.headers.get("Authorization") == f"JWT {TOKEN}"

			def do_GET(self):
				if not self._authed():
					return self._send(401, {"detail": "Authentication credentials were not provided."})
				u = urlparse(self.path)
				q = {k: v[0] for k, v in parse_qs(u.query).items()}
				src = {"/personnel/api/employees/": (fake.employees, "emp_code"),
				       "/personnel/api/departments/": (fake.departments, "dept_code"),
				       "/personnel/api/areas/": (fake.areas, "area_code")}.get(u.path)
				if not src:
					return self._send(404, {"detail": "Not found."})
				rows, key = src
				# Like BioTime: the code filter is a 'contains', not exact.
				if q.get(key):
					rows = [r for r in rows if q[key] in str(r.get(key))]
				self._send(200, {"count": len(rows), "next": None, "previous": None, "data": rows})

			def do_POST(self):
				u = urlparse(self.path).path
				body = self._body()
				if u == "/jwt-api-token-auth/":
					if body.get("username") == fake.username and body.get("password") == fake.password:
						return self._send(200, {"token": TOKEN})
					return self._send(400, {"non_field_errors": ["Unable to log in with provided credentials."]})
				if not self._authed():
					return self._send(401, {"detail": "Authentication credentials were not provided."})
				fake.calls.append(("POST", u))
				if u == "/personnel/api/employees/":
					if any(e["emp_code"] == body.get("emp_code") for e in fake.employees):
						return self._send(400, {"emp_code": ["Employee with this code already exists."]})
					fake._next += 1
					row = {**body, "id": fake._next}
					fake.employees.append(row)
					return self._send(201, row)
				if u == "/personnel/api/resigns/":
					fake.resigns.append(body)
					return self._send(201, {**body, "id": len(fake.resigns)})
				self._send(404, {"detail": "Not found."})

			def do_PATCH(self):
				if not self._authed():
					return self._send(401, {"detail": "no"})
				u = urlparse(self.path).path
				fake.calls.append(("PATCH", u))
				emp_id = int(u.rstrip("/").split("/")[-1])
				row = next((e for e in fake.employees if e["id"] == emp_id), None)
				if not row:
					return self._send(404, {"detail": "Not found."})
				row.update(self._body())
				self._send(200, row)

		self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
		threading.Thread(target=self.server.serve_forever, daemon=True).start()
		return self

	def stop(self):
		self.server.shutdown()
		self.server.server_close()

	def add(self, emp_code, first_name, area_ids=(2,)):
		self._next += 1
		self.employees.append({"id": self._next, "emp_code": emp_code, "first_name": first_name,
		                       "last_name": "", "department": 1, "area": list(area_ids)})
		return self._next


if __name__ == "__main__":
	# Handy for a manual run: python3 tests/fake_easytime.py
	import time

	s = FakeEasyTime().start()
	print(f"Fake Easy Time Pro on {s.url} (user api / secret). Ctrl-C to stop.")
	try:
		while True:
			time.sleep(1)
	except KeyboardInterrupt:
		s.stop()
