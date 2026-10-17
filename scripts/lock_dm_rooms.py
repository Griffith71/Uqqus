"""Gives every existing 1:1 chat room the locked-down power levels new rooms get (audit M-02: before, either person
could invite a third account, which both clients would then encrypt to). Also removes anyone in a room who isn't one
of its two people. Run once after deploying:

    PYTHONPATH=. python scripts/lock_dm_rooms.py --once
"""
import sys

from flask import g

from ruqqus.__main__ import app, db_session
from ruqqus.classes.chat import ChatConversation
from ruqqus.helpers import matrix_client as matrix


def main():
    fixed = removed = failed = 0
    with app.test_request_context("/"):
        g.db = db_session()
        for conv in g.db.query(ChatConversation).all():
            if not conv.matrix_room_id:
                continue
            try:
                matrix.lock_down_room(conv.matrix_room_id)
                fixed += 1
                allowed = {matrix.mxid_for(conv.user_a_id), matrix.mxid_for(conv.user_b_id)}
                for mxid in matrix.room_member_ids(conv.matrix_room_id):
                    bot = f"@{app.config['MATRIX_BOT_LOCALPART']}:{app.config['MATRIX_SERVER_NAME']}"
                    if mxid != bot and mxid not in allowed:
                        matrix.kick_as_bot(conv.matrix_room_id, mxid, "Not part of this chat")
                        removed += 1
            except matrix.MatrixError as e:
                failed += 1
                print(f"{conv.matrix_room_id}: {e}")
    print(f"locked {fixed} rooms, removed {removed} extra members, {failed} failed")


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(1)
    main()
