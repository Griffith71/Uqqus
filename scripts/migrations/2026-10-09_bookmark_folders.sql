-- Bookmark folders (helpers/bookmark_folders.py): a member's own folders for sorting what they
-- bookmarked. A bookmark is in at most one folder; no folder (NULL) means "unsorted". Deleting a
-- folder never deletes bookmarks (they go back to unsorted).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-09_bookmark_folders.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS bookmark_folders (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    name character varying(40) NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL
);

-- a name is unique per member, ignoring case
CREATE UNIQUE INDEX IF NOT EXISTS bookmark_folders_name_key ON bookmark_folders (user_id, lower(name));

ALTER TABLE save_relationship ADD COLUMN IF NOT EXISTS folder_id integer;
ALTER TABLE comment_save_relationship ADD COLUMN IF NOT EXISTS folder_id integer;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'save_relationship_folder_fkey') THEN
        ALTER TABLE save_relationship ADD CONSTRAINT save_relationship_folder_fkey
            FOREIGN KEY (folder_id) REFERENCES bookmark_folders(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'comment_save_relationship_folder_fkey') THEN
        ALTER TABLE comment_save_relationship ADD CONSTRAINT comment_save_relationship_folder_fkey
            FOREIGN KEY (folder_id) REFERENCES bookmark_folders(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS save_relationship_folder_index ON save_relationship (user_id, folder_id);
CREATE INDEX IF NOT EXISTS comment_save_relationship_folder_index ON comment_save_relationship (user_id, folder_id);
