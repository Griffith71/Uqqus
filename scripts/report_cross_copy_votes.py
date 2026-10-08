"""Count the people who have an active vote on more than one copy of the same post.

Since the rule "one vote per person across the copies of a post" (helpers/vote_copies.py) votes cast
before it were left as they are. This only READS the database and changes nothing: it says how many such
people there are, split into the post's author (who gets an automatic upvote on every forward copy and is
exempt from the rule) and everyone else, with a few examples.

    PYTHONPATH=. python scripts/report_cross_copy_votes.py [--examples 5]
"""
import argparse

from sqlalchemy import text

from ruqqus.__main__ import db_session

# the same notion of "family" and "live" as helpers/vote_copies.py
PEOPLE = text("""
    SELECT v.user_id AS user_id,
           COALESCE(NULLIF(s.repost_id, 0), s.id) AS family,
           COUNT(*) AS votes,
           BOOL_OR(v.user_id = s.author_id) AS is_author
    FROM votes v
    JOIN submissions s ON s.id = v.submission_id
    JOIN boards b ON b.id = s.board_id
    WHERE v.vote_type <> 0
      AND COALESCE(s.is_banned, false) = false AND COALESCE(s.deleted_utc, 0) = 0 AND COALESCE(b.is_banned, false) = false
    GROUP BY v.user_id, COALESCE(NULLIF(s.repost_id, 0), s.id)
    HAVING COUNT(*) > 1
    ORDER BY votes DESC, family
""")

NAMES = text("SELECT id, username FROM users WHERE id = ANY(:ids)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--examples", type=int, default=5, help="how many examples of each kind to list")
    args = parser.parse_args()

    db = db_session()
    rows = db.execute(PEOPLE).fetchall()
    authors = [row for row in rows if row.is_author]
    others = [row for row in rows if not row.is_author]

    names = {}
    if rows:
        ids = list({row.user_id for row in rows})
        names = {row.id: row.username for row in db.execute(NAMES, {"ids": ids})}

    print(f"people with an active vote on more than one copy of the same post: {len(rows)}")
    print(f"  the post's own author (automatic upvote on each copy, exempt): {len(authors)}")
    print(f"  everyone else (the rule now stops new ones): {len(others)}")

    for title, group in (("everyone else", others), ("authors", authors)):
        if group:
            print(f"\nexamples, {title}:")
            for row in group[:args.examples]:
                print(f"  @{names.get(row.user_id, row.user_id)}: {row.votes} votes on the copies of post family {row.family}")

    db.close()


if __name__ == "__main__":
    main()
