"""Command line entry point.

	python -m grohr_agent            run the loop
	python -m grohr_agent --once     one cycle, then exit (use this to test a setup)
	python -m grohr_agent --status   what is in the spool
	python -m grohr_agent --probe    test every database source in config.json
	                                 (read-only, needs no server) and describe it
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
	parser.add_argument("--verbose", action="store_true")
	parser.add_argument("--version", action="version", version=__version__)
	args = parser.parse_args(argv)

	try:
		config = load(args.config)
	except ConfigError as e:
		print(f"Configuration error: {e}", file=sys.stderr)
		return 2

	log = setup(config.log_dir, verbose=args.verbose)

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
