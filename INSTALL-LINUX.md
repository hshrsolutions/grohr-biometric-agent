# Installing on Linux

For offices whose always-on machine is a Linux server — for example a customer
that hosts its own GroHR site on the LAN, where the agent runs on that same box.
It installs as a **systemd service** (`Restart=always`, starts at boot).

## One command

From the unzipped release, as root:

```bash
sudo bash install/linux/install.sh --customer <profile>
```

It will:

1. check for Python 3.10+ and `python3-venv`
2. copy the agent to `/opt/grohr-biometric-agent` and build a venv there
3. write `config.json` from `profiles/<profile>/config.json`, **asking** for the
   API key/secret and any database login (typed, never stored in the profile)
4. make `config.json` root-only (`0600`)
5. run `--probe` (if there are database sources) and one real `--once` cycle
6. only if both pass, install and start `grohr-biometric-agent.service`

Re-running it is an **update**: the service is stopped, files replaced, and
`config.json`, the spool and the logs are kept.

Options: `--dir PATH`, `--no-service` (test only), `--force` (install the
service even though a check failed — don't).

## Before you start

From the GroHR site administrator: the site address, the **Biometric Connector**
name, and an API key/secret for a user holding **only** `Biometric Integration`.

If the site is on the same machine (a self-hosted bench), use
`site_url: http://127.0.0.1:<web port>` with `host_header: <site name>` —
the `thenianantham` profile does exactly this.

## Operating it

```bash
systemctl status grohr-biometric-agent
tail -f /opt/grohr-biometric-agent/logs/agent.log
cd /opt/grohr-biometric-agent && venv/bin/python -m grohr_agent --status
cd /opt/grohr-biometric-agent && venv/bin/python -m grohr_agent --probe
```

## Removing it

```bash
systemctl disable --now grohr-biometric-agent
rm /etc/systemd/system/grohr-biometric-agent.service && systemctl daemon-reload
```

Check `--status` shows `pending: 0` before deleting `/opt/grohr-biometric-agent`
— the spool holds punches the server has not received yet.

SQL Server sources on Linux additionally need unixODBC and Microsoft's
`msodbcsql17` package; MySQL/MariaDB sources need nothing extra.
