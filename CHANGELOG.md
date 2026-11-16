# Changelog

## 0.5.0 — 2026-10-27

- **Monthly punch tables** (`table_pattern`, e.g. `DeviceLogs_{m}_{Y}`): reads eSSL
  eTimeTrackLite / eTimeTrack, which keep one table per month. Every month from the
  window start to now, oldest first; months without a table are skipped. `--probe`
  checks the newest monthly table.
- **`tools/essl_discover.py`**: read-only commissioning tool. Connects with the login
  you give it (all visible databases, or one), recognises `iclock_transaction`
  (Time Pro/WDMS, Easy Time Pro, BioTime) and `DeviceLogs_<m>_<y>` (eTimeTrack), lists the
  reader serials with punch counts and flags stale readers, prints the `sources` block,
  and says plainly when a table looks like a variant that is not supported.
  `--json` for the installer. Offline tests 106.

## 0.4.0 — 2026-10-03

- **Push employees into eSSL Easy Time Pro.** The heartbeat reply can carry
  `user_pushes` (create/update or remove one employee); the agent applies them
  through Easy Time Pro's own REST API (`grohr_agent/easytime.py`: JWT or Token
  login, `/personnel/api/employees/`, `/resigns/`, area/department codes resolved
  to ids) and posts outcomes to `user_push_results`. Easy Time Pro then sends the
  user to the machines in their area. Never writes the Easy Time Pro database.
- Never overwrites a user found under the same code unless the server says
  `overwrite` — a mismatch comes back as `Exists` for HR.
- `easytime.dry_run` (looks up, writes nothing) for commissioning, and
  `--probe-easytime` (log in, list departments and areas, change nothing).
- Optional `easytime` block in config.json; without it pushes are answered with a
  clear error and punches are unaffected. Offline tests 87 (fake Easy Time Pro
  in `tests/fake_easytime.py`).

## 0.3.0 — 2026-10-03

- Answers the server's profile requests (heartbeat `profile_requests` → read-only lookup of name + photo → `profiles` API), so a device ID changed in GroHR shows the machine photo. Optional `profile` block in config.json.
- `tools/export_photos.py`: read-only bulk export of employee photos (`--probe`, blob/base64/file path), packed in the hand-over zip.

## 0.3.0 — 2026-10-03

- `tools/export_punches.py`: one-off, read-only export of the attendance database
  to CSV (punches, per-day summary, `people.csv` with identity columns only) for a
  backfill or audit with biometric_hub's `tools/backfill_csv.py`.

## 0.2.0 — 2026-09-24

- **MySQL / MariaDB sources.** New `SQL Database` reader for attendance software
  that keeps punches in a database (eTimeTrack, BioTime, ZKTime, …); `WDMS` is now
  the same reader with SQL Server as its default. Engine-correct quoting,
  placeholders and `LIMIT`/`TOP`; identifiers validated; MySQL session set READ ONLY.
- **Database credentials live only in `config.json` under `sources`** (by device
  name, serial or `"*"`). Before this, the WDMS reader expected a connection string
  the server never sent, so it could not run end to end.
- **Look-back window + sent-ledger.** Reads start `lookback_minutes` (default 24 h)
  before the server cursor, so late uploads from offline readers are caught; the
  spool remembers delivered punches (`sent_retention_days`, default 45) so the
  overlap is not re-sent. `>=` on the cursor, so same-second punches are kept.
- **Paged reads** (`max_rows`, `max_pages`) and a first-run floor (`Ignore Punches
  Before` or `initial_days`) — no history flood, no year of rows in memory.
- **`--probe`**: read-only commissioning report for every source.
- **Installers**: `install/windows/install.ps1` (service, auto start, restart on
  failure, config locked to SYSTEM/Administrators) and `install/linux/install.sh`
  (systemd). Both refuse to install the service until `--probe`/`--once` pass.
- **Customer profiles** under `profiles/` (bhima, thenianantham) and `pack.sh`,
  which builds the hand-over zip and refuses to pack a profile containing a secret.
- Requirements: `PyMySQL` added; Windows extras split into `requirements-windows.txt`.

## 0.1.0 — 2026-08-31

First release: ZKTeco LAN reader, WDMS (SQL Server) reader, Fake reader, spool,
batching, heartbeat, Windows service. Hard fork of
`frappe/biometric-attendance-sync-tool`.
