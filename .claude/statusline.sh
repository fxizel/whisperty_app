#!/bin/bash
input=$(cat)

eval "$(echo "$input" | python -c "
import json, sys, shlex
from datetime import datetime, timezone

d = json.load(sys.stdin)
model = d.get('model', {}).get('display_name', '')
cdir  = d.get('workspace', {}).get('current_dir', d.get('cwd', ''))
dirname = cdir.replace('\\\\', '/').rstrip('/').split('/')[-1]
cw    = d.get('context_window', {})
pct   = int(cw.get('used_percentage', 0) or 0)

def parse_reset(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None

# Quota 5h : pourcentage + heure locale de reset
rl5      = d.get('rate_limits', {}).get('five_hour', {})
rl_pct   = int(rl5.get('used_percentage', 0) or 0)
rl_reset = ''
reset_dt = parse_reset(rl5.get('resets_at'))
if reset_dt and reset_dt > datetime.now(timezone.utc):
    rl_reset = reset_dt.astimezone().strftime('%Hh%M')

# Quota glissant 7 jours
rl7   = d.get('rate_limits', {}).get('seven_day', {})
w_pct = int(rl7.get('used_percentage', 0) or 0)

print('MODEL='     + shlex.quote(str(model)))
print('DIR='       + shlex.quote(str(dirname)))
print('PCT='       + str(pct))
print('RL_PCT='    + str(rl_pct))
print('RL_RESET='  + shlex.quote(rl_reset))
print('WEEK_PCT='  + str(w_pct))
" 2>/dev/null)"

PCT=${PCT:-0}
RL_PCT=${RL_PCT:-0}; RL_RESET=${RL_RESET:-}
WEEK_PCT=${WEEK_PCT:-0}

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'

# Barre context window
if [ "$PCT" -ge 90 ]; then BAR_COLOR="$RED"
elif [ "$PCT" -ge 70 ]; then BAR_COLOR="$YELLOW"
else BAR_COLOR="$GREEN"; fi

FILLED=$((PCT / 10)); EMPTY=$((10 - FILLED))
printf -v FILL "%${FILLED}s"; printf -v PAD "%${EMPTY}s"
BAR="${FILL// /█}${PAD// /░}"

BRANCH=""
git rev-parse --git-dir > /dev/null 2>&1 && BRANCH=" | 🌿 $(git branch --show-current 2>/dev/null)"

echo -e "${CYAN}[$MODEL]${RESET} 📁 ${DIR}$BRANCH"
RL_PART=""
[ "$RL_PCT" -gt 0 ] && RL_PART=" | 🔄 5h ${RL_PCT}%"
[ -n "$RL_RESET" ] && RL_PART="${RL_PART} | ⏳ ${RL_RESET}"
[ "$WEEK_PCT" -gt 0 ] && RL_PART="${RL_PART} | 📆 7j ${WEEK_PCT}%"
echo -e "${BAR_COLOR}${BAR}${RESET} ${PCT}%${RL_PART}"
