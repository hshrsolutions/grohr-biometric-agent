"""Find ZKTeco / eSSL terminals on the local network.

Opens a plain TCP connection to the ZK port (default 4370) on each address of a
private subnet, then — for the ones that answer — asks the terminal to describe
itself (read-only, same as --probe-zk). Nothing is written to any device.

Limits, on purpose: private (RFC 1918) addresses only and at most 1024 hosts per
scan, so it cannot be pointed at the internet. A terminal that only answers UDP
is not found by a TCP scan — enter its IP by hand and tick Force UDP.
"""

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

from grohr_agent.devices.zkteco import ZKTecoReader

MAX_HOSTS = 1024


class ScanError(ValueError):
	pass


def local_subnet():
	"""This PC's /24, found without sending a packet (UDP connect only picks a
	route). None if there is no usable network."""
	try:
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		s.connect(("10.255.255.255", 1))
		ip = s.getsockname()[0]
		s.close()
		net = ipaddress.ip_network(f"{ip}/24", strict=False)
		return str(net) if net.is_private else None
	except OSError:
		return None


def hosts_in(cidr):
	try:
		net = ipaddress.ip_network(str(cidr).strip(), strict=False)
	except ValueError:
		raise ScanError(f"{cidr!r} is not a network like 192.168.1.0/24.")
	if net.version != 4:
		raise ScanError("Only IPv4 networks are scanned.")
	if not net.is_private:
		raise ScanError("Only private networks (10.x, 172.16-31.x, 192.168.x) are scanned.")
	if net.num_addresses > MAX_HOSTS:
		raise ScanError(f"That is {net.num_addresses} addresses; scan at most {MAX_HOSTS} (a /22) at a time.")
	return [str(h) for h in (net.hosts() if net.num_addresses > 2 else net)]


def _open(ip, port, timeout):
	try:
		with socket.create_connection((ip, port), timeout=timeout):
			return True
	except OSError:
		return False


def scan(cidr=None, ports=(4370,), timeout=0.4, password=0, identify=True, workers=128):
	"""Returns a list of {ip, port, status, ...info}. status is 'ok' (identified),
	'needs_key' (port open, ZK would not talk without the Comm Key) or 'open'
	(port open, not identified)."""
	cidr = cidr or local_subnet()
	if not cidr:
		raise ScanError("Could not work out this PC's network; give a network such as 192.168.1.0/24.")
	targets = [(ip, int(p)) for ip in hosts_in(cidr) for p in ports]

	with ThreadPoolExecutor(max_workers=workers) as pool:
		flags = list(pool.map(lambda t: _open(t[0], t[1], timeout), targets))
	found = [t for t, up in zip(targets, flags) if up]

	def identify_one(target):
		ip, port = target
		row = {"ip": ip, "port": port, "status": "open"}
		if not identify:
			return row
		device = {"name": "scan", "serial_no": None, "ip_address": ip, "port": port,
		          "zk": {"password": int(password or 0), "timeout": 5, "ommit_ping": True,
		                 "verify_serial": False}}
		try:
			info = ZKTecoReader(device, None).describe()
			row.update(status="ok", serial=info.get("serial"), device=info.get("device"),
			           firmware=info.get("firmware"), mac=info.get("mac"))
		except Exception as e:
			row.update(status="needs_key", error=f"{type(e).__name__}: {e}")
		return row

	with ThreadPoolExecutor(max_workers=8) as pool:
		rows = list(pool.map(identify_one, found))
	rows.sort(key=lambda r: tuple(int(x) for x in r["ip"].split(".")) + (r["port"],))
	return {"network": cidr, "scanned": len(targets), "found": rows}
