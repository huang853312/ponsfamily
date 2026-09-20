#!/usr/bin/env bash
# Only this monitor's paths and service may be modified.
set -euo pipefail
test "$(id -u)" = 0
release_source=${1:?release source required}
release_sha=${2:?commit SHA required}
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$release_source" == /tmp/chain-flow-monitor.* ]] || exit 2
root=/opt/chain-flow-monitor
unit=chain-flow-monitor.service
config=/etc/chain-flow-monitor.env
test -s "$config"

# Parse as data, never source a shell file or print a secret.
python3 - "$config" <<'PY'
import json, sys, urllib.request, urllib.error
from pathlib import Path
if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10+ required')
c = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    key, sep, value = line.partition('=')
    if not sep or key.strip() in c:
        raise SystemExit('Invalid or duplicate config entry')
    c[key.strip()] = value.strip().strip('\"\'')
for key in ('FLOW_TELEGRAM_BOT_TOKEN', 'FLOW_TELEGRAM_CHAT_ID'):
    if not c.get(key):
        raise SystemExit('Missing dedicated setting: ' + key)
base = 'https://api.telegram.org/bot' + c['FLOW_TELEGRAM_BOT_TOKEN']
try:
    def call(method, payload):
        req = urllib.request.Request(base + '/' + method, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
        if not body.get('ok'):
            raise ValueError('API failure')
        return body['result']
    bot = call('getMe', {})
    if bot.get('username') != 'zijin_alert_bot':
        raise SystemExit('Configured bot is not the independently verified bot; stopping')
    chat = call('getChat', {'chat_id': c['FLOW_TELEGRAM_CHAT_ID']})
    if chat.get('type') != 'channel':
        raise SystemExit('Configured destination is not a channel; stopping')
    member = call('getChatMember', {'chat_id': chat['id'], 'user_id': bot['id']})
    if member.get('status') != 'creator' and not (member.get('status') == 'administrator' and member.get('can_post_messages')):
        raise SystemExit('Bot lacks channel posting permission')
    print('PREFLIGHT_OK: dedicated bot and channel publishing permission verified')
except urllib.error.HTTPError as e:
    raise SystemExit('Telegram preflight HTTP ' + str(e.code)) from None
except (OSError, ValueError, KeyError):
    raise SystemExit('Telegram preflight failed; credentials not printed') from None
PY

cd "$release_source"
python3 -m unittest -v
install -d -m 755 "$root/releases/$release_sha"
for file in monitor.py test_monitor.py chain-flow-monitor.service README.md; do
    install -m 644 "$file" "$root/releases/$release_sha/$file"
done
printf '%s\n' "$release_sha" > "$root/releases/$release_sha/REVISION"
chmod 600 "$config"

previous=$(readlink "$root/current" || true)
if test -e "$root/current" && ! test -L "$root/current"; then
    echo 'Refusing to replace a non-symlink current path'
    exit 1
fi
previous_active=false
if systemctl is-active --quiet "$unit"; then previous_active=true; fi
backup_unit="$root/releases/$release_sha/previous.service"
if test -f "/etc/systemd/system/$unit"; then
    cp -p "/etc/systemd/system/$unit" "$backup_unit"
fi

rollback() {
    echo 'New monitor failed verification; rolling back only chain-flow-monitor'
    systemctl stop "$unit" || true
    if test -n "$previous"; then
        ln -sfn "$previous" "$root/current.rollback"
        mv -Tf "$root/current.rollback" "$root/current"
    fi
    if test -f "$backup_unit"; then
        install -m 644 "$backup_unit" "/etc/systemd/system/$unit"
    fi
    systemctl daemon-reload
    if "$previous_active"; then systemctl start "$unit" || true; fi
}
trap rollback ERR

ln -sfn "$root/releases/$release_sha" "$root/current.next"
mv -Tf "$root/current.next" "$root/current"
install -m 644 "$root/current/chain-flow-monitor.service" "/etc/systemd/system/$unit"
systemctl daemon-reload
started_at=$(date +%s)
systemctl restart "$unit"

# A process being active is insufficient: require a fresh report accepted by Telegram.
verified=false
for attempt in $(seq 1 72); do
    if python3 - "$started_at" <<'PY'
import json, sys
from pathlib import Path
try:
    h = json.loads(Path('/var/lib/chain-flow-monitor/health.json').read_text())
    assert h['collected_at'] >= int(sys.argv[1])
    assert h['last_sent_at'] >= int(sys.argv[1])
    assert h['tvl_chains'] > 0
except (OSError, ValueError, KeyError, AssertionError):
    sys.exit(1)
print('FIRST_REPORT_DELIVERED', json.dumps(h))
PY
    then
        verified=true
        break
    fi
    if ! systemctl is-active --quiet "$unit"; then break; fi
    sleep 5
done
"$verified"
systemctl is-active --quiet "$unit"
systemctl enable "$unit"
trap - ERR
printf 'DEPLOY_SUCCESS revision=%s service=%s\n' "$release_sha" "$unit"
systemctl show "$unit" -p ActiveState -p SubState -p MainPID -p NRestarts
