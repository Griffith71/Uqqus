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
if [ "$high_known" -gt "$mark" ]; then
  echo "NOTE: the chat server's mark ($mark) is below the site's chat accounts ($high_known): written by a site from before the mark; the app records it when it starts."
fi
if [ "$mark" -gt 0 ] && [ "$next_id" -le "$mark" ]; then
  echo "NOTE: new user ids would start at $next_id, which already had a chat account: the app moves them past $mark when it starts."
fi
