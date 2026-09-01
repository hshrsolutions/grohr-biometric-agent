"""Windows service wrapper.

Installed as a real service, not a Scheduled Task and not a console someone left
open: it survives logout, restarts on failure, and starts with the machine.

	python agent_win.py install
	net start GroHRBiometricAgent
	python agent_win.py update      # after replacing the files
	net stop GroHRBiometricAgent

The service runs from its own directory, so config.json and the spool sit beside
the executable regardless of who started it.
"""

import os
import sys

from SMWinservice import SMWinservice


class GroHRBiometricAgent(SMWinservice):
	_svc_name_ = "GroHRBiometricAgent"
	_svc_display_name_ = "GroHR Biometric Agent"
	_svc_description_ = "Reads biometric attendance punches and submits them to GroHR."

	def start(self):
		self.running = True

	def stop(self):
		self.running = False

	def main(self):
		os.chdir(os.path.dirname(os.path.abspath(__file__)))
		sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

		from grohr_agent.agent import Agent
		from grohr_agent.config import load
		from grohr_agent.logging_setup import setup

		config = load("config.json")
		log = setup(config.log_dir)
		Agent(config, log).run_forever(stop=lambda: not self.running)


if __name__ == "__main__":
	GroHRBiometricAgent.parse_command_line()
