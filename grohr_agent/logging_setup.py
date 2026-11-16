"""Rotating file log, plus console when run interactively.

10MB x 5, not upstream's 10MB x 50 per device — half a gigabyte of logs per
reader is not a diagnostic, it is a disk problem.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup(log_dir, verbose=False):
	os.makedirs(log_dir, exist_ok=True)
	logger = logging.getLogger("grohr_agent")
	logger.setLevel(logging.DEBUG if verbose else logging.INFO)
	logger.handlers.clear()

	formatter = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")

	handler = RotatingFileHandler(
		os.path.join(log_dir, "agent.log"), maxBytes=10_000_000, backupCount=5, encoding="utf-8"
	)
	handler.setFormatter(formatter)
	logger.addHandler(handler)

	if sys.stdout and sys.stdout.isatty():
		console = logging.StreamHandler(sys.stdout)
		console.setFormatter(formatter)
		logger.addHandler(console)

	return logger
