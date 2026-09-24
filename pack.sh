#!/usr/bin/env bash
# Build the customer hand-over zip: dist/grohr-biometric-agent-<version>.zip
# Contains the agent, installers, profiles and docs - never config.json, a spool,
# logs or tests. Run the offline tests first; refuses to pack if they fail.
set -euo pipefail
cd "$(dirname "$0")"
python3 tests/test_agent.py >/dev/null || { echo "offline tests failed - not packing"; exit 1; }
VERSION=$(python3 -c 'import grohr_agent; print(grohr_agent.__version__)')
NAME=grohr-biometric-agent-$VERSION
rm -rf "dist/$NAME" && mkdir -p "dist/$NAME"
tar --exclude=__pycache__ --exclude='*.pyc' -cf - grohr_agent install profiles agent_win.py SMWinservice.py \
	requirements.txt requirements-windows.txt config.example.json README.md INSTALL-WINDOWS.md INSTALL-LINUX.md \
	CHANGELOG.md LICENSE | tar -C "dist/$NAME" -xf -
if grep -rE '"(api_secret|password)": *"[^"]+' "dist/$NAME/profiles" "dist/$NAME/config.example.json"; then
	echo "a profile contains a secret - refusing to pack"; exit 1
fi
( cd dist && rm -f "$NAME.zip" && python3 -m zipfile -c "$NAME.zip" "$NAME" )
( cd dist && sha256sum "$NAME.zip" > "$NAME.zip.sha256" )
echo "dist/$NAME.zip"; cat "dist/$NAME.zip.sha256"
