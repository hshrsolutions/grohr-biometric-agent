"""Single-instance guard.

Two copies on a shared machine double every punch. Upstream has no guard at all;
on a machine several people log into, that is not a hypothetical.
"""

import atexit
import os
import sys


class AlreadyRunning(Exception):
	pass


def acquire(name="GroHRBiometricAgent", lock_path=None):
	if sys.platform == "win32":
		return _acquire_windows(name)
	return _acquire_posix(lock_path or os.path.join(os.path.expanduser("~"), f".{name}.lock"))


def _acquire_windows(name):
	import ctypes

	kernel32 = ctypes.windll.kernel32
	handle = kernel32.CreateMutexW(None, False, f"Global\\{name}")
	if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
		raise AlreadyRunning("Another copy of the agent is already running on this machine.")
	atexit.register(kernel32.CloseHandle, handle)
	return handle


def _acquire_posix(lock_path):
	import fcntl

	handle = open(lock_path, "w")
	try:
		fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
	except OSError:
		raise AlreadyRunning("Another copy of the agent is already running on this machine.")
	handle.write(str(os.getpid()))
	handle.flush()
	atexit.register(handle.close)
	return handle
