# GroHR Biometric Agent

Reads biometric attendance punches on a customer's premises and submits them to
a Frappe/HRMS site running [Biometric Hub](https://github.com/) over outbound
HTTPS.

This is a hard fork of
[`frappe/biometric-attendance-sync-tool`](https://github.com/frappe/biometric-attendance-sync-tool)
(BAST) and, like it, is licensed **GPL-3.0**. The server-side app it talks to is
a separate MIT program; the two communicate only over HTTP.

## The idea

**The agent is dumb and replaceable.** It holds no business logic, decides
nothing about employees, shifts or log types, and stores no state that matters.
It reads punches and posts them. Every rule lives on the server, where it can be
fixed without physical access to a customer's office.

That is the one thing this fork changes, and everything else follows from it.

## What it does differently from upstream

| Upstream BAST | Here |
|---|---|
| The sync cursor is the **last line of a rotating log file** — rotate or clear the logs and it re-imports everything | The cursor is server-side; the agent asks for it at bootstrap. A stale one is harmless because the server key is idempotent |
| **One HTTP request per punch** | Batched, 200 at a time |
| A permanently rejected punch is written to a failed-log and **skipped forever**, and the cursor moves past it | The server stages every punch *before* mapping, so an unmappable punch sits in a work queue instead of vanishing |
| Any unrecognised error **aborts the whole device's batch** | One bad punch fails one punch |
| The agent **PUTs `last_sync_of_checkin` onto `Shift Type`**, so its credential needs write access to an HR master | The server does it. This credential can create checkins and nothing else |
| Credentials live in `local_config.py`, **an executable Python module** | Inert `config.json`, validated on load |
| **Disables the device** for the length of every fetch | Never. The reader stays live on the door |
| Offers `clear_from_device_on_fetch` | Not offered. Upstream's own comment calls it a data-loss risk |
| **No heartbeat** — the server cannot tell "no punches" from "died three weeks ago" | Heartbeat every cycle, with the agent version |
| **No single-instance guard** | Named mutex on Windows, lock file elsewhere |
| Devices polled sequentially; one dead reader delays the rest | A reader that fails, fails alone |
| Device list, punch direction and shift mapping live in the client config | All server-side; adding a reader is a desk action, not a site visit |

## ZKTeco LX50 (direct, no attendance software)

Register the terminal in GroHR (driver `ZKTeco`, its LAN IP, port 4370, serial from the
terminal's *Menu → System Info*), then from the agent PC on the same network:

```bash
python -m grohr_agent --probe-zk 192.168.1.201            # read-only: serial, firmware, counts
python -m grohr_agent --probe-zk 192.168.1.201 --zk-password 1234   # if a Comm Key is set
python -m grohr_agent --probe-zk 192.168.1.201 --zk-udp             # if TCP times out
```

Prefer a page to a terminal? `python -m grohr_agent --ui` and open
`http://127.0.0.1:8765` — the same test, a preview of the newest punches, and the
spool/log status. It listens on this PC only and is read-only.

If the probe needs a Comm Key or UDP, put the same in `config.json` → `zkteco`
(see `config.example.json`). Then `--once` to prove the whole path.

## Install

One command per platform, from the unzipped release
(`./pack.sh` builds `dist/grohr-biometric-agent-<version>.zip`):

| Platform | Command | Guide |
|---|---|---|
| Windows (reference) | `powershell -ExecutionPolicy Bypass -File .\install\windows\install.ps1 -Customer <profile>` | [INSTALL-WINDOWS.md](INSTALL-WINDOWS.md) |
| Linux (systemd) | `sudo bash install/linux/install.sh --customer <profile>` | [INSTALL-LINUX.md](INSTALL-LINUX.md) |

Both installers ask for the secrets, lock `config.json`, **prove the setup with
`--probe` and `--once` before installing the service**, and are safe to re-run
as an update. Customer profiles (site, connector, source defaults — never
secrets) live in `profiles/<customer>/config.json`.

By hand, anywhere:

```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json     # then fill it in
python -m grohr_agent --once           # one cycle, to prove the setup
python -m grohr_agent                  # run
```

## Configuration

| Key | Meaning |
|---|---|
| `site_url` | e.g. `https://yourcompany.grohrms.cloud` |
| `api_key`, `api_secret` | Of a user holding **only** the `Biometric Integration` role |
| `connector` | The `Biometric Connector` record this agent is |
| `poll_interval_seconds` | Default 300 |
| `batch_size` | Default 200, maximum 1000 |
| `max_attempts` | Before a punch is dead-lettered locally. Default 10 |
| `verify_tls` | Leave `true` |
| `proxy`, `host_header` | For networks that need them |
| `log_dir`, `spool_path` | Relative paths resolve beside `config.json` |

| `sent_retention_days` | How long delivered punches are remembered locally. Default 45 |
| `sources` | Attendance-software databases — see below |

Lock down `config.json` — it holds a live credential.

## Attendance-software databases (MySQL / MariaDB / SQL Server)

When the readers talk only to the vendor's software and the punches live in its
database, register each reader in GroHR with driver **`SQL Database`** (or
`WDMS` for eSSL Time Pro, which defaults to SQL Server) and tell the agent how to
reach that database under `sources`:

```json
"sources": {
  "*": {
    "engine": "mysql",               // or "mssql" (SQL Server)
    "host": "127.0.0.1", "port": 3306,
    "database": "etimetrack", "user": "grohr_ro", "password": "…",
    "table": "iclock_transaction",
    "user_column": "emp_code", "time_column": "punch_time",
    "serial_column": "terminal_sn",  // null if the table has none
    "state_column": "punch_state",   // null if the table has none
    "lookback_minutes": 1440, "initial_days": 7, "max_rows": 5000
  }
}
```

Keys are matched in order: device **name**, device **serial**, then `"*"`.
SQL Server also accepts `connection_string`, or `trusted_connection: true`
instead of `user`/`password`, and `odbc_driver`.

- **Read-only.** SELECT only; on MySQL the session is put in READ ONLY mode too.
  Give it a login with SELECT on the one table — nothing else.
- **Credentials stay on the premises.** The server's device list never carries
  them.
- **Late uploads are caught.** Each read starts `lookback_minutes` before the
  server's cursor; the local sent-ledger means that overlap is not re-sent.
- **No history flood.** A first read starts at the device's *Ignore Punches
  Before*, or `initial_days` ago.
- **Commission it first:** `python -m grohr_agent --probe` connects to every
  source, lists the tables if the configured one is missing, checks the columns,
  prints the row count and date range, **every reader serial in the table** (register
  each as a Biometric Device) and the latest punches. It needs no server.

### Finding out which eSSL database this is (0.5.0+)

```bash
ESSL_DB_PASSWORD=... python tools/essl_discover.py --engine mssql --host 192.168.20.5 --user grohr_ro
```

Read-only. Recognises `iclock_transaction` (Time Pro / WDMS / Easy Time Pro / BioTime) and eTimeTrackLite's
monthly `DeviceLogs_<m>_<yyyy>` tables, lists each reader with its punch count (and flags stale ones), compares
the database server's clock, and prints the `sources` block. `--json` is what the Thenianantham installer uses.

For monthly tables use `table_pattern` instead of `table`:
`"table_pattern": "DeviceLogs_{m}_{Y}", "user_column": "UserId", "time_column": "LogDate", "serial_column": "DeviceId", "state_column": "Direction"`
(`{m}` month 1-12, `{mm}` 01-12, `{Y}` year; every month from the read window to now is read, missing months skipped).

## Pushing employees into eSSL Easy Time Pro (0.4.0+)

GroHR can create, update and remove employees in Easy Time Pro (eSSL's build of
ZKTeco BioTime), so a new joiner reaches the machines without being typed in
twice. The server decides who and where; this agent only calls Easy Time Pro's
REST API on the office PC, and Easy Time Pro sends the user to the machines in
their area. The Easy Time Pro database is never written.

1. In Easy Time Pro, make a user for the agent (System → User) with rights to
   Personnel → Employee and Resignation. Not the admin login.
2. Add to `config.json`:

       "easytime": {"url": "http://127.0.0.1:8081", "username": "grohr_sync",
                    "password": "…", "auth": "jwt", "dry_run": true}

   `auth` is `"token"` on older BioTime builds that have no JWT login. If this
   build's URLs differ, override them under `"paths"` (see `grohr_agent/easytime.py`).
3. `python -m grohr_agent --probe-easytime` — logs in and lists departments and
   areas. Changes nothing. The area codes go into GroHR's Biometric Settings.
4. In GroHR switch on Biometric Settings → Push Employees to Easy Time Pro, with
   this agent's connector. With `dry_run` on, every request comes back as
   *Dry Run* in GroHR's **Easy Time Pro Sync** list, showing what would happen.
5. When the dry run looks right: set `"dry_run": false`, restart the service, and
   press **Resend all Dry Run** in GroHR.

A user Easy Time Pro already has under the same ID is only updated if the names
match; anyone else comes back to HR as a **Conflict**, untouched. Fingerprints and
faces are still enrolled at a machine — no software can create them remotely.

## Operating it

```bash
python -m grohr_agent --status    # what is waiting locally
python -m grohr_agent --probe     # test the database sources (read-only, no server needed)
python -m grohr_agent --once      # a single cycle, verbose, for diagnosis
```

`logs/agent.log` rotates at 10MB, five deep.

**Where a punch can be.** Read from the device → the local spool → accepted by
the server. Once the server has it, the agent forgets it: anything the server
could not map is in *its* work queue, visible in the desk, and is not the
agent's problem. The only punches the agent still holds are ones the server has
never seen — `--status` counts them, and `dead` counts those it has given up
sending, which always means something needs a human.

## Commissioning without hardware

Set a device's driver to `Fake` and point `GROHR_AGENT_FAKE_PUNCHES` at a JSON
file of `{device_user_id, punch_time}` objects. The whole path — credentials,
network, mapping, checkins — can then be proven before anyone mounts a reader.

## Licence

GPL-3.0. Derived from `frappe/biometric-attendance-sync-tool`.
`SMWinservice.py` is by Davide Mastromatteo, retained from upstream.
