"""The only thing this agent knows about the outside world.

Outbound HTTPS only: no inbound port, no VPN, no firewall change. Everything it
sends is idempotent, so a timeout is never a reason to guess.
"""

import time

import requests


class ServerError(Exception):
	"""Something went wrong that is worth retrying."""


class AuthError(Exception):
	"""Credentials are wrong or revoked. Retrying faster will not help."""


class Server:
	def __init__(self, config, logger):
		self.config = config
		self.log = logger
		self.session = requests.Session()
		self.session.headers.update(
			{
				"Authorization": f"token {config.api_key}:{config.api_secret}",
				"Accept": "application/json",
				"Content-Type": "application/json",
				"User-Agent": f"grohr-biometric-agent/{_version()}",
			}
		)
		if config.get("host_header"):
			self.session.headers["Host"] = config["host_header"]
		if config.get("proxy"):
			self.session.proxies.update({"http": config["proxy"], "https": config["proxy"]})

	def _call(self, method, payload):
		url = f"{self.config.site_url}/api/method/biometric_hub.api.ingest.{method}"
		try:
			response = self.session.post(
				url,
				json=payload,
				timeout=self.config.request_timeout_seconds,
				verify=self.config.verify_tls,
			)
		except requests.RequestException as e:
			raise ServerError(f"{type(e).__name__}: {e}")

		if response.status_code in (401, 403):
			raise AuthError(
				f"HTTP {response.status_code} from {method}. Check the API key and secret, and that "
				"the user still holds the Biometric Integration role."
			)
		if response.status_code >= 400:
			raise ServerError(f"HTTP {response.status_code} from {method}: {_body(response)[:300]}")

		try:
			return response.json().get("message")
		except ValueError:
			raise ServerError(f"{method} returned something that is not JSON: {_body(response)[:200]}")

	# ------------------------------------------------------------------ calls

	def bootstrap(self):
		return self._call("bootstrap", {"connector": self.config.connector})

	def heartbeat(self, agent_version, devices=None):
		return self._call(
			"heartbeat",
			{"connector": self.config.connector, "agent_version": agent_version, "devices": devices or []},
		)

	def profiles(self, profiles):
		return self._call("profiles", {"connector": self.config.connector, "profiles": profiles})

	def user_push_results(self, results):
		return self._call("user_push_results", {"connector": self.config.connector, "results": results})

	def push(self, punches):
		return self._call("push", {"connector": self.config.connector, "punches": punches})


def _body(response):
	try:
		return response.text
	except Exception:
		return "<unreadable>"


def _version():
	from grohr_agent import __version__

	return __version__


def backoff(attempt, base=5, cap=600):
	"""Exponential, capped. Upstream has no back-off at all, which is how one customer
	turned a 255-employee data gap into 38,000 requests a day."""
	return min(cap, base * (2 ** min(attempt, 10)))


def sleep_with_backoff(attempt, log=None):
	delay = backoff(attempt)
	if log:
		log.info(f"Backing off {delay}s (attempt {attempt}).")
	time.sleep(delay)
