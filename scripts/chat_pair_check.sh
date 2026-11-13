#!/usr/bin/env bash
# Whether a site database and a Synapse database belong together (chat plan, phase 1; helpers/chat_health.py says why).
# Run inside the backup container, which has psql for both servers:
#
#   bash chat_pair_check.sh <site database url> <synapse database url>
#
# Every chat account the site knows (chat_identities) must be on the chat server. The backup service dumps the site's
# database first and Synapse's second, so a good pair always passes; a failure means the two are not a pair. It also says
# where the chat server's high-water mark is, which the app moves new user ids past when it starts. Exit 1 on a failure.
set -euo pipefail
site="$1"
syn="$2"
q() { psql "$1" -v ON_ERROR_STOP=1 -At -c "$2"; }

known=$(q "$site" "SELECT count(*) FROM chat_identities")
high_known=$(q "$site" "SELECT coalesce(max(user_id), 0) FROM chat_identities")
next_id=$(q "$site" "SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END FROM users_id_seq")
mark=$(q "$syn" "SELECT coalesce(max((content::json->>'high')::bigint), 0) FROM account_data WHERE account_data_type = 'org.uqqus.chat_ids'")
missing=0
if [ "$known" -gt 0 ]; then
  missing=$(q "$site" "SELECT matrix_user_id FROM chat_identities" |
    psql "$syn" -v ON_ERROR_STOP=1 -At -c "CREATE TEMP TABLE k (m text)" -c "\\copy k FROM pstdin" \
      -c "SELECT count(*) FROM k WHERE m NOT IN (SELECT name FROM users)" | tail -n 1)
fi

echo "chat: $known chat accounts on the site, $missing of them missing on the chat server; highest id with a chat account $mark; next new user id $next_id"
if [ "$missing" -gt 0 ]; then
  echo "FAIL: the chat server lacks $missing chat accounts the site knows: these two databases are not a pair (RESTORE.md)."
  exit 1
fi
# The chat devices the site made and still serves (chat_site_devices, from 2026-12-20_chat_sign_outs.sql on) should be on the chat
# server. A few may not be in a good pair (one signed out between the two dumps); many mean the site's dump is older than Synapse's
# or a device was deleted on Synapse by hand. Said, never a failure (RESTORE.md "After restoring an older pair").
if [ "$(q "$site" "SELECT to_regclass('chat_site_devices') IS NOT NULL")" = "t" ]; then
  live=$(q "$site" "SELECT count(*) FROM chat_site_devices WHERE revoked_utc = 0")
  gone=0
  if [ "$live" -gt 0 ]; then
    gone=$(q "$site" "SELECT i.matrix_user_id || E'\\t' || d.device_id FROM chat_site_devices d JOIN chat_identities i ON i.user_id = d.user_id WHERE d.revoked_utc = 0" |
      psql "$syn" -v ON_ERROR_STOP=1 -At -c "CREATE TEMP TABLE s (m text, d text)" -c "\\copy s FROM pstdin" \
        -c "SELECT count(*) FROM s WHERE NOT EXISTS (SELECT 1 FROM devices v WHERE v.user_id = s.m AND v.device_id = s.d)" | tail -n 1)
  fi
  echo "chat devices: $live the site still serves, $gone of them not on the chat server"
  if [ "$gone" -gt 0 ]; then
    echo "NOTE: $gone chat devices the site still serves aren't on the chat server: after restoring an older pair, sign every chat device out (RESTORE.md)."
  fi
fi
if [ "$high_known" -gt "$mark" ]; then
  echo "NOTE: the chat server's mark ($mark) is below the site's chat accounts ($high_known): written by a site from before the mark; the app records it when it starts."
fi
if [ "$mark" -gt 0 ] && [ "$next_id" -le "$mark" ]; then
  echo "NOTE: new user ids would start at $next_id, which already had a chat account: the app moves them past $mark when it starts."
fi
