#!/usr/bin/env bash
# Install or update the GroHR Biometric Agent as a systemd service.
#
#   sudo bash install/linux/install.sh --customer thenianantham
#
# Copies the agent to /opt/grohr-biometric-agent, builds a venv, writes
# config.json from the customer profile (asking for the secrets), locks it to
# root, proves the setup with --probe and --once, then installs and starts
# grohr-biometric-agent.service (Restart=always, starts at boot).
#
# Re-running is an update: config.json, the spool and the logs are kept.
#
# Options:
#   --customer NAME   profile under profiles/ (used only when there is no config.json)
#   --dir PATH        install dir (default /opt/grohr-biometric-agent)
#   --no-service      install and test only
#   --force           install the service even if the checks failed
set -euo pipefail

CUSTOMER=""; DIR=/opt/grohr-biometric-agent; NO_SERVICE=0; FORCE=0
while [[ $# -gt 0 ]]; do
	case "$1" in
		--customer) CUSTOMER="$2"; shift 2 ;;
		--dir) DIR="$2"; shift 2 ;;
		--no-service) NO_SERVICE=1; shift ;;
		--force) FORCE=1; shift ;;
		-h|--help) sed -n 2,20p "$0"; exit 0 ;;
		*) echo "unknown option $1"; exit 2 ;;
	esac
done

say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
good() { printf '\033[32m    OK  %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    !!  %s\033[0m\n' "$*"; }
die()  { printf '\033[31m    XX  %s\033[0m\n' "$*"; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
UNIT=grohr-biometric-agent

say "Python"
PY=$(command -v python3 || true)
[[ -n "$PY" ]] || die "python3 not found (apt install python3 python3-venv)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "python 3.10+ needed"
"$PY" -c 'import venv' 2>/dev/null || die "python3-venv missing (apt install python3-venv)"
good "$("$PY" --version)"

if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
	say "Stopping the running service for the update"; systemctl stop "$UNIT"; good stopped
fi

say "Copying the agent to $DIR"
mkdir -p "$DIR"
if [[ "$SRC" != "$(cd "$DIR" && pwd)" ]]; then
	tar -C "$SRC" --anchored --exclude=./config.json --exclude=./spool.sqlite3 --exclude=./logs --exclude=__pycache__ \
		--exclude=.git --exclude=venv --exclude=dist --exclude=tests -cf - . | tar -C "$DIR" -xf -
fi
good "files in place (config.json, spool and logs untouched)"

say "Python packages (venv)"
[[ -x "$DIR/venv/bin/python" ]] || "$PY" -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --disable-pip-version-check -q --upgrade -r "$DIR/requirements.txt"
good "requests, pyzk, PyMySQL"
if grep -q '"mssql"' "$DIR/config.json" "$DIR/profiles/${CUSTOMER:-none}/config.json" 2>/dev/null; then
	"$DIR/venv/bin/pip" install -q pyodbc || warn "pyodbc failed - SQL Server sources also need unixODBC + msodbcsql17"
fi

CFG="$DIR/config.json"
if [[ ! -f "$CFG" ]]; then
	TEMPLATE="$DIR/config.example.json"
	if [[ -n "$CUSTOMER" ]]; then
		TEMPLATE="$DIR/profiles/$CUSTOMER/config.json"
		[[ -f "$TEMPLATE" ]] || die "no profile '$CUSTOMER' (have: $(ls "$DIR/profiles" | tr '\n' ' '))"
	fi
	say "Writing config.json from $TEMPLATE"
	"$DIR/venv/bin/python" - "$TEMPLATE" "$CFG" <<'PY'
import getpass, json, sys
cfg = json.load(open(sys.argv[1]))
def ask(prompt, current, secret=False):
    if current and not str(current).startswith("CHANGE-ME"):
        return current
    return (getpass.getpass if secret else input)(prompt + ": ")
cfg["site_url"] = ask("GroHR site address", cfg.get("site_url"))
cfg["connector"] = ask("Biometric Connector name", cfg.get("connector"))
cfg["api_key"] = ask("API key of the Biometric Integration user", cfg.get("api_key"))
cfg["api_secret"] = ask("API secret", cfg.get("api_secret"), secret=True)
for name, src in (cfg.get("sources") or {}).items():
    if name.startswith("_"):
        continue
    print(f"Attendance database for source {name!r} ({src.get('engine')})")
    for key, prompt, secret in (("host", "  host", False), ("database", "  database", False),
                                ("user", "  login (SELECT-only)", False), ("password", "  password", True)):
        src[key] = ask(prompt, src.get(key), secret)
json.dump(cfg, open(sys.argv[2], "w"), indent=2)
PY
	good "config.json written"
else
	good "keeping the existing config.json"
fi
chown root:root "$CFG"; chmod 600 "$CFG"
good "config.json is root-only (0600)"

FAILED=0
cd "$DIR"
if "$DIR/venv/bin/python" -c "import json,sys; s=json.load(open('$CFG')).get('sources') or {}; sys.exit(0 if [k for k in s if not k.startswith('_')] else 1)"; then
	say "Probing the attendance database (read-only)"
	"$DIR/venv/bin/python" -m grohr_agent --config "$CFG" --probe || { FAILED=1; warn "--probe failed"; }
fi
say "One real cycle against the site"
"$DIR/venv/bin/python" -m grohr_agent --config "$CFG" --once --verbose || { FAILED=1; warn "--once failed; see $DIR/logs/agent.log"; }

if [[ $FAILED -eq 1 && $FORCE -eq 0 ]]; then die "not installing the service until the checks pass (or --force)"; fi
[[ $NO_SERVICE -eq 1 ]] && { good "tests done; service not installed (--no-service)"; exit 0; }

say "systemd service"
cat > /etc/systemd/system/$UNIT.service <<UNITFILE
[Unit]
Description=GroHR Biometric Agent - biometric punches to GroHR
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python -m grohr_agent --config $CFG
Restart=always
RestartSec=60
User=root

[Install]
WantedBy=multi-user.target
UNITFILE
systemctl daemon-reload
systemctl enable --now "$UNIT" >/dev/null
sleep 3
systemctl is-active --quiet "$UNIT" || die "service did not start: journalctl -u $UNIT"
good "$UNIT is active and enabled at boot"
echo
echo "Log:    $DIR/logs/agent.log"
echo "Status: cd $DIR && venv/bin/python -m grohr_agent --status"
