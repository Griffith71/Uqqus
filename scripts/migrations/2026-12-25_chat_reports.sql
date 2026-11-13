-- Reports of messages in private chats (helpers/chat_reports.py): the reporter's browser sends the reported message and
-- the few before it, as it shows them; who sent each and when come from the chat server. The copied words are emptied
-- 90 days after a report is closed (helpers/retention.py). No foreign keys to users (the project's rule). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-25_chat_reports.sql
CREATE TABLE IF NOT EXISTS public.chat_reports (
    id SERIAL PRIMARY KEY,
    reporter_id integer NOT NULL,
    room_id character varying(255) NOT NULL,
    room_kind character varying(8) NOT NULL,
    event_id character varying(255) NOT NULL,
    reported_id integer,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer NOT NULL,
    status character varying(10) DEFAULT 'open' NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    resolved_by_id integer,
    CONSTRAINT chat_reports_once UNIQUE (reporter_id, event_id),
    CONSTRAINT chat_reports_kind_check CHECK (room_kind IN ('pair', 'group')),
    CONSTRAINT chat_reports_status_check CHECK (status IN ('open', 'dismissed', 'actioned'))
);
CREATE INDEX IF NOT EXISTS chat_reports_open_idx ON public.chat_reports (status, created_utc);
CREATE INDEX IF NOT EXISTS chat_reports_reporter_idx ON public.chat_reports (reporter_id, created_utc);

CREATE TABLE IF NOT EXISTS public.chat_report_messages (
    report_id integer NOT NULL REFERENCES public.chat_reports(id) ON DELETE CASCADE,
    position smallint NOT NULL,
    event_id character varying(255) NOT NULL,
    sender_id integer,
    sender_mxid character varying(255) NOT NULL,
    sent_ms bigint NOT NULL,
    body text DEFAULT '' NOT NULL,
    PRIMARY KEY (report_id, position)
);
