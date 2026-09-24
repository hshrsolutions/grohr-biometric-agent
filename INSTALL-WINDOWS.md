# Installing on Windows

The reference deployment. It runs as a **Windows service**, not a Scheduled Task
and not a console window someone left open — so it survives logout, restarts
after a crash, and starts with the machine.

## The one-command install

1. Copy `grohr-biometric-agent-<version>.zip` to the machine and unzip it
   (anywhere — the installer copies it to `C:\GroHR\biometric-agent`).
2. Open **PowerShell as Administrator**, `cd` into the unzipped folder, run:

```
powershell -ExecutionPolicy Bypass -File .\install\windows\install.ps1 -Customer bhima
```

The installer:

| Step | What happens |
|---|---|
| Python | Finds Python 3.10+; if missing and `winget` exists, installs 3.12 for all users, otherwise tells you where to get it |
| Files | Copies to `C:\GroHR\biometric-agent`, keeping any existing `config.json`, spool and logs |
| Packages | `requests`, `pyzk`, `PyMySQL`, `pyodbc`, `pywin32` |
| Config | From `profiles\<customer>\config.json`, **asking** for the API key/secret and the attendance-database login and password |
| Lock | `config.json` readable by SYSTEM and Administrators only |
| Prove | `--probe` (the database) and `--once` (a real cycle against the site). **If either fails, it stops here** and no service is installed |
| Service | `GroHRBiometricAgent`, start type Automatic, restart after 1 minute on failure |

Re-run the same command to **update**. Options: `-InstallDir`, `-NoService`
(test only), `-Force` (install despite a failed check — don't).
`install\windows\uninstall.ps1` removes the service and keeps the files.

## Before you start

You need, from whoever administers the GroHR site:

- the site address, e.g. `https://yourcompany.grohrms.cloud`
- an **API key and secret** for a user holding only the `Biometric Integration`
  role (that user can create attendance punches and nothing else — if someone
  offers you an Administrator key, decline it)
- the **connector name** — the `Biometric Connector` record created for this
  office

And on the machine itself, one of:

- **readers on the LAN**: their IP addresses, and confirmation it can reach them
  on port 4370; or
- **punches in the attendance software's database** (MySQL/MariaDB or SQL
  Server): its host, database name, and a login with **SELECT on the punch
  table only**. For MySQL, e.g.
  `CREATE USER 'grohr_ro'@'localhost' IDENTIFIED BY '…'; GRANT SELECT ON etime.iclock_transaction TO 'grohr_ro'@'localhost';`

The machine needs **outbound HTTPS only**. No inbound port, no VPN, no firewall
exception.

---

# Manual install (if the script cannot be used)

## 1. Put the files somewhere permanent

`C:\GroHR\biometric-agent` is a good choice. Not a user's Desktop or Downloads —
the service runs whether or not that person is logged in, and their profile may
not survive them leaving.

## 2. Install Python and the dependencies

Python 3.10 or newer, from python.org, ticking **Add Python to PATH**.

```
cd C:\GroHR\biometric-agent
python -m pip install -r requirements.txt -r requirements-windows.txt
```

## 3. Write the configuration

```
copy config.example.json config.json
notepad config.json
```

Fill in `site_url`, `api_key`, `api_secret` and `connector`. Leave the rest.

Then restrict the file, because it holds a live credential:

```
icacls config.json /inheritance:r
icacls config.json /grant "SYSTEM:(R)" "Administrators:(F)"
```

## 4. Prove it works before installing the service

```
python -m grohr_agent --once --verbose
```

You want to see the bootstrap listing your devices, punches read, and the server
accepting them. If it cannot authenticate it will say so in those words.

Check the site as well: the `Biometric Connector` should now show a recent
**Last Check-in** and an **Agent Version**.

## 5. Install the service

```
python agent_win.py install
net start GroHRBiometricAgent
```

Confirm in `services.msc` that **GroHR Biometric Agent** is *Running* and set to
*Automatic*. Under its **Recovery** tab, set all three failure actions to
**Restart the Service**.

## 6. Confirm it is alive

- `logs\agent.log` shows a cycle every five minutes.
- The `Biometric Connector` in the desk updates its **Last Check-in** every
  cycle, whether or not there were any punches. That is deliberate: silence
  means something is wrong, and it should be visible without anyone asking.

## Updating

```
net stop GroHRBiometricAgent
```

Replace the files, keeping `config.json` and `spool.sqlite3`, then:

```
python agent_win.py update
net start GroHRBiometricAgent
```

Nothing is lost across an update: unsent punches are in the spool, and anything
already sent is on the server.

## When something is wrong

| What you see | What it means |
|---|---|
| `AUTHENTICATION FAILED` | The key or secret is wrong, or the user lost the `Biometric Integration` role. Nothing is lost; punches are held locally |
| `Server unreachable` | Network or site outage. Punches are held locally and sent when it returns |
| `could not read` on one device | That reader only. Check the IP and that port 4370 is open — or, for a database source, run `--probe` |
| `has no \`sources\` entry` | The device's driver reads a database but `config.json` has no matching `sources` key (device name, serial or `*`) |
| `dead-lettered` | The agent gave up sending. **These never reached the server** — send `logs\agent.log` and the spool file to support |
| `Another copy of the agent is already running` | Working as intended — two copies would double every punch |
| Punches arriving but nobody marked present | Not an agent problem. The site's **Biometric Workbench** will show them as unmapped device IDs waiting to be matched to employees |

## Building a single .exe (optional)

```
python -m pip install pyinstaller
python -m PyInstaller --name grohr-biometric-agent --onefile grohr_agent\__main__.py
```

`config.json` still lives beside the executable. The service wrapper needs the
Python install, so use the `.exe` for hand-run diagnosis and the service for
production.
