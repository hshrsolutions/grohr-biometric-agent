"""Command line entry point.

	python -m grohr_agent            run the loop
	python -m grohr_agent --once     one cycle, then exit (use this to test a setup)
	python -m grohr_agent --status   what is in the spool
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
		print(f"pending: {counts['pending']}\ndead:    {counts['dead']}")
		return 0

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


if __name__ == "__main__":
	sys.exit(main())
