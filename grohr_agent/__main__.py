"""Command line entry point.

	python -m grohr_agent            run the loop
	python -m grohr_agent --once     one cycle, then exit (use this to test a setup)
	python -m grohr_agent --status   what is in the spool
	python -m grohr_agent --probe    test every database source in config.json
	                                 (read-only, needs no server) and describe it
	python -m grohr_agent --probe-zk IP [--zk-port 4370] [--zk-password KEY] [--zk-udp]
	                                 connect straight to a ZKTeco/eSSL terminal (LX50, ...)
	                                 and describe it; read-only, needs no server
	python -m grohr_agent --scan-zk [192.168.1.0/24] [--zk-password KEY]
	                                 find ZKTeco terminals on the LAN (port 4370), read-only
	python -m grohr_agent --ui [--ui-port 8765]
	                                 local web page (127.0.0.1 only): test a terminal,
	                                 preview its punches, see spool and log
	python -m grohr_agent --probe-easytime
	                                 log in to Easy Time Pro (config `easytime`) and
	                                 list its departments and areas; changes nothing
"""

import argparse
import sys

from grohr_agent import __version__
from grohr_agent.agent import Agent
from grohr_agent.config import ConfigError, load
from grohr_agent.lock import AlreadyRunning, acquire
from grohr_agent.logging_setup import setup
from grohr_agent.spool import Spool


def main(argv=None):
	parser = argparse.ArgumentParser(prog="grohr-biometric-agent")
	parser.add_argument("--config", default="config.json")
	parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
	parser.add_argument("--status", action="store_true", help="print spool counts and exit")
	parser.add_argument("--probe", action="store_true",
	                    help="connect to each database source in config.json and describe it (read-only)")
	parser.add_argument("--probe-easytime", action="store_true",
	                    help="log in to Easy Time Pro and list departments and areas (read-only)")
	parser.add_argument("--probe-zk", metavar="IP",
	                    help="connect directly to a ZKTeco terminal and describe it (read-only)")
	parser.add_argument("--scan-zk", nargs="?", const="auto", metavar="NETWORK",
	                    help="scan a private network (default: this PC's /24) for ZKTeco terminals")
	parser.add_argument("--zk-port", type=int, default=4370)
	parser.add_argument("--zk-password", type=int, default=0, help="terminal Comm Key")
	parser.add_argument("--zk-udp", action="store_true", help="force UDP")
	parser.add_argument("--ui", action="store_true", help="serve the local web UI on 127.0.0.1")
	parser.add_argument("--ui-port", type=int, default=8765)
	parser.add_argument("--verbose", action="store_true")
	parser.add_argument("--version", action="version", version=__version__)
	args = parser.parse_args(argv)

	if args.scan_zk:
		from grohr_agent.discover_zk import ScanError, scan

		try:
			result = scan(None if args.scan_zk == "auto" else args.scan_zk, ports=(args.zk_port,),
			              password=args.zk_password)
		except ScanError as e:
			print(f"Scan error: {e}")
			return 2
		print(f"Scanned {result['scanned']} addresses on {result['network']} (port {args.zk_port}).")
		for r in result["found"]:
			label = {"ok": f"{r.get('device')} serial {r.get('serial')} fw {r.get('firmware')}",
			         "needs_key": "port open, not identified (Comm Key set? try --zk-password)",
			         "open": "port open"}[r["status"]]
			print(f"  {r['ip']}:{r['port']}  {label}")
		if not result["found"]:
			print("  none found. UDP-only terminals need --probe-zk IP --zk-udp.")
		return 0 if result["found"] else 1

	if args.probe_zk:
		from grohr_agent.devices.zkteco import ZKTecoReader

		print(f"ZKTeco terminal {args.probe_zk}:{args.zk_port}")
		reader = ZKTecoReader({"name": "probe", "serial_no": None, "ip_address": args.probe_zk,
		                       "port": args.zk_port,
		                       "zk": {"password": args.zk_password, "force_udp": args.zk_udp}}, None)
		return 0 if reader.probe() else 1

	try:
		config = load(args.config)
	except ConfigError as e:
		if args.ui:
			# Commissioning before config.json exists: the probe still works.
			from grohr_agent.config import Config

			print(f"Note: {e}\nStarting the UI without a config (status will be empty).")
			from grohr_agent.ui import serve

			return serve(Config(log_dir="logs", spool_path="spool.sqlite3"), args.ui_port)
		print(f"Configuration error: {e}", file=sys.stderr)
		return 2

	log = setup(config.log_dir, verbose=args.verbose)

	if args.ui:
		from grohr_agent.ui import serve

		return serve(config, args.ui_port)

	if args.status:
		counts = Spool(config.spool_path).counts()
		print(f"pending: {counts['pending']}\ndead:    {counts['dead']}\nsent:    {counts['sent']} (remembered)")
		return 0

	if args.probe:
		return probe(config)

	if args.probe_easytime:
		from grohr_agent import easytime

		try:
			return easytime.probe(config.get("easytime"))
		except easytime.EasyTimeError as e:
			print(f"  FAILED: {e}")
			return 1

	try:
		acquire()
	except AlreadyRunning as e:
		log.error(str(e))
		return 3

	agent = Agent(config, log)
	if args.once:
		return 0 if agent.cycle() else 1
	try:
		agent.run_forever()
	except KeyboardInterrupt:
		log.info("Interrupted.")
	return 0


def probe(config):
	from grohr_agent.devices.sqldb import SQLReader

	sources = {k: v for k, v in (config.get("sources") or {}).items() if not k.startswith("_")}
	if not sources:
		print("No `sources` in config.json — nothing to probe. (ZKTeco readers are probed by --once.)")
		return 2
	ok = True
	for key, source in sources.items():
		print(f"Source {key!r}: {source.get('engine', 'mysql')} {source.get('host') or '(connection string)'}"
		      f" / {source.get('database', '')} / table {source.get('table', 'iclock_transaction')}")
		device = {"name": key, "serial_no": None if key == "*" else key, "source": source}
		try:
			ok = SQLReader(device, None).probe() and ok
		except Exception as e:
			ok = False
			print(f"  FAILED: {type(e).__name__}: {e}")
		print()
	print("All sources OK." if ok else "One or more sources need attention (see above).")
	return 0 if ok else 1


if __name__ == "__main__":
	sys.exit(main())
