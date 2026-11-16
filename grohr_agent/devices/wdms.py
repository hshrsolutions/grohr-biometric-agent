"""eSSL Time Pro / WDMS — the vendor's SQL Server database.

Kept as its own driver name because devices on existing sites are registered
against it. It is the SQL Database reader with SQL Server as the default engine
and the `iclock_transaction` schema observed on a real Time Pro installation.
See `sqldb.py` for everything else, including why it is strictly read-only.
"""

from grohr_agent.devices.sqldb import SQLReader


class WDMSReader(SQLReader):
	driver_name = "WDMS"
	default_engine = "mssql"
