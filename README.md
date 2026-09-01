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

## Install

Windows is the reference platform — see **[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md)**.

Anywhere else:

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

Lock down `config.json` — it holds a live credential.

## Operating it

```bash
python -m grohr_agent --status    # what is waiting locally
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
