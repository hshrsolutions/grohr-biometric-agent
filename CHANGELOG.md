# Changelog

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
