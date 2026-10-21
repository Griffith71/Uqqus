"""Gives every chat account the name outside Matrix apps show (Settings > Chat apps): the member's @handle, "Deleted
account" for a deleted one, and the site's name for its bot (helpers/chat_devices.py). New accounts and handle
changes are named as they happen; run this once after deploying, and again after restoring a backup:

    PYTHONPATH=. python scripts/sync_chat_names.py --once
"""
import sys

from ruqqus.__main__ import app, db_session  # first: it patches ssl for gevent

from ruqqus.classes import User
from ruqqus.classes.chat import ChatIdentity
from ruqqus.helpers import chat_devices
from ruqqus.helpers import matrix_client as matrix


def main():
    named = failed = 0
    with app.app_context():
        db = db_session()
        bot = f"@{app.config['MATRIX_BOT_LOCALPART']}:{app.config['MATRIX_SERVER_NAME']}"
        try:
            matrix.set_display_name(bot, app.config["SITE_NAME"])
            named += 1
        except matrix.MatrixError as e:
            print(f"the bot: {e}", flush=True)
            failed += 1
        rows = db.query(ChatIdentity.matrix_user_id, User).join(User, User.id == ChatIdentity.user_id).all()
        for mxid, user in rows:
            try:
                matrix.set_display_name(mxid, chat_devices.name_for(user))
                named += 1
            except matrix.MatrixError as e:
                print(f"{mxid}: {e}", flush=True)
                failed += 1
    print(f"named {named}, failed {failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    if "--once" not in sys.argv:
        print(__doc__)
        sys.exit(2)
    sys.exit(main())
