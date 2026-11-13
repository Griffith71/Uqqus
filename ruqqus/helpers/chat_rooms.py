"""Who is in a chat, for the routes that act on one by its Matrix room id (a report, a file sent in it). Plain `text()`
SQL with bound values, tested on SQLite; no ORM model, so the star import of ruqqus.classes can't replace it.

A 1:1 chat's two people are in it whichever tab it is in (a message request included); a group's members are in it
joined or invited (an invitation reads the group from then on). Channels and guild channels have their own rules."""
import re

from sqlalchemy import text

ROOM_ID = re.compile(r"^![A-Za-z0-9_\-+/=]{8,128}(:[A-Za-z0-9.\-]{1,255}(:\d{1,5})?)?$")


def kind_for(db, room_id, user_id):
    """'pair' or 'group' when `user_id` is in this chat, otherwise None."""
    if not room_id or not ROOM_ID.match(room_id) or not user_id:
        return None
    pair = db.execute(text("SELECT user_a_id, user_b_id FROM chat_conversations WHERE matrix_room_id = :r"), {"r": room_id}).first()
    if pair is not None:
        return "pair" if user_id in (pair[0], pair[1]) else None
    member = db.execute(text("""SELECT 1 FROM chat_group_members m JOIN chat_groups g ON g.id = m.group_id
                                WHERE g.matrix_room_id = :r AND g.deleted_utc = 0 AND m.user_id = :u"""),
                        {"r": room_id, "u": user_id}).first()
    return "group" if member is not None else None
