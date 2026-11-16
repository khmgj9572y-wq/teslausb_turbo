#!/bin/bash
# Adds explicit sidecar rules; does not rewrite upstream sudo or web auth policy.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo 'Run as root' >&2; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
user=${1:?Pass the non-root AirPlay desktop account}
[[ "$user" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Invalid desktop account name' >&2; exit 1; }
[ "$(id -u "$user")" -ne 0 ] || { echo 'Desktop account must not be root' >&2; exit 1; }
[ "$user" != car-portal ]
command -v xauth >/dev/null || { echo 'Install xauth before configuring the portal' >&2; exit 1; }
was_ro=false
case ",$(findmnt -no OPTIONS /)," in *,ro,*) was_ro=true;; esac
cleanup() {
  result=$?; trap - EXIT; sync
  if $was_ro && ! mount -o remount,ro /; then result=1; echo 'ERROR: root remains writable' >&2; fi
  exit "$result"
}
mount -o remount,rw /
trap cleanup EXIT
backup=$(mktemp -d /root/car-web-privileges.XXXXXX)
chmod 700 "$backup"
if ! id car-portal >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin car-portal
fi
if [ "$(id -u car-portal)" -eq 0 ] || id -nG car-portal | grep -Eq '(^| )(sudo|wheel|admin)( |$)' || runuser -u car-portal -- sudo -n true >/dev/null 2>&1; then
  echo 'Dedicated portal account has unexpected sudo access; stopped' >&2; exit 1
fi
python3 - "$backup" <<'PY'
from pathlib import Path
import sys,re,shutil
p=Path('/etc/teslausb-car-wifi.env')
text=p.read_text() if p.exists() else ''
if p.exists():shutil.copy2(p,Path(sys.argv[1])/'car-wifi.env')
text=re.sub(r'^(PORTAL_USER|DISPLAY_USER)=.*\n?', '', text, flags=re.M).rstrip()+'\nPORTAL_USER=car-portal\n'
p.write_text(text);p.chmod(0o600)
PY
printf 'DISPLAY_USER=%s\n' "$user" >> /etc/teslausb-car-wifi.env
python3 - "$backup/rules" "$user" <<'PY'
from pathlib import Path
import sys
lib='/usr/local/lib/teslausb-car-link/'
wifi='status scan add delete permanent retry portal-start portal-check portal-stop'.split()
video='video-status video-start video-stop'.split()
rules=['www-data ALL=(root) NOPASSWD: '+', '.join(lib+'wifi_admin.py '+a for a in wifi)]
rules+=['www-data ALL=(root) NOPASSWD: '+', '.join(lib+'video_web.py '+a for a in video)]
rules+=[sys.argv[2]+' ALL=(root) NOPASSWD: '+lib+'display_owner.py claim *, '+lib+'display_owner.py release *']
Path(sys.argv[1]).write_text('\n'.join(rules)+'\n')
PY
visudo -cf "$backup/rules" >/dev/null
install -m 440 "$backup/rules" /etc/sudoers.d/car-web-sidecar
install -m 755 "$src/portal_session.sh" /usr/local/lib/teslausb-car-link/portal_session.sh
echo 'Dedicated portal account and explicit sidecar sudo rules installed'
echo 'Audit upstream sudo policy separately: blanket grants still dominate these rules'
