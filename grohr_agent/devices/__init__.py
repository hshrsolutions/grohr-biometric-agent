"""Device readers.

Deliberately mirrors the server's driver split: a reader turns a protocol into
punch dictionaries and does nothing else.
"""

from grohr_agent.devices.fake import FileReader
from grohr_agent.devices.wdms import WDMSReader
from grohr_agent.devices.zkteco import ZKTecoReader

READERS = {"ZKTeco": ZKTecoReader, "Fake": FileReader, "WDMS": WDMSReader}


def reader_for(driver_name):
	return READERS.get(driver_name)
