--
-- PostgreSQL database dump
--

-- Dumped from database version 12.5
-- Dumped by pg_dump version 12.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: fuzzystrmatch; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS fuzzystrmatch WITH SCHEMA public;


--
-- Name: EXTENSION fuzzystrmatch; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION fuzzystrmatch IS 'determine similarities and distance between strings';


--
-- Name: pg_stat_statements; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;


--
-- Name: EXTENSION pg_stat_statements; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_stat_statements IS 'track execution statistics of all SQL statements executed';


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: boards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.boards (
    id integer NOT NULL,
    name character varying(64),
    is_banned boolean,
    created_utc integer,
    description character varying(1500),
    description_html character varying(5000),
    is_sensitive boolean,
    creator_id integer,
    has_banner boolean NOT NULL,
    has_profile boolean NOT NULL,
    ban_reason character varying(256),
    color character varying(8),
    downvotes_disabled boolean,
    restricted_posting boolean,
    hide_banner_data boolean,
    profile_nonce integer NOT NULL,
    banner_nonce integer NOT NULL,
    is_private boolean,
    color_nonce integer,
    is_nsfl boolean,
    rank_trending double precision,
    stored_subscriber_count integer,
    avg_score double precision,
    all_opt_out boolean,
    is_siegable boolean DEFAULT true,
    last_yank_utc integer DEFAULT 0,
    is_locked_category boolean DEFAULT false,
    subcat_id integer,
    secondary_color character(6) DEFAULT 'ffffff'::bpchar,
    public_chat boolean DEFAULT false,
    motd character varying(1000) DEFAULT ''::character varying,
    disallowbots boolean DEFAULT false,
    css_nonce integer DEFAULT 0,
    css character varying(65536) DEFAULT ''::character varying,
    name_severity smallint DEFAULT 0 NOT NULL,
    description_severity smallint DEFAULT 0 NOT NULL
);


--
-- Name: age(public.boards); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.age(public.boards) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$

      SELECT CAST( EXTRACT( EPOCH FROM CURRENT_TIMESTAMP) AS int) - $1.created_utc
      $_$;


--
-- Name: comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comments (
    id integer NOT NULL,
    author_id integer,
    created_utc integer NOT NULL,
    parent_submission integer,
    is_banned boolean,
    parent_fullname character varying(255),
    distinguish_level integer,
    edited_utc integer,
    deleted_utc integer NOT NULL,
    is_approved integer NOT NULL,
    approved_utc integer,
    creation_ip character varying(64) NOT NULL,
    score_disputed double precision,
    score_hot double precision,
    score_top integer,
    level integer,
    parent_comment_id integer,
    title_id integer,
    is_op boolean,
    is_offensive boolean,
    is_sensitive boolean,
    original_board_id integer,
    upvotes integer,
    downvotes integer,
    is_bot boolean DEFAULT false,
    gm_distinguish integer DEFAULT 0 NOT NULL,
    is_pinned boolean DEFAULT false,
    app_id integer,
    creation_region character(2) DEFAULT NULL::bpchar,
    purged_utc integer DEFAULT 0,
    hidden_by_guild boolean DEFAULT false,
    author_hidden boolean DEFAULT false,
    word_severity smallint DEFAULT 0 NOT NULL,
    word_filter_version character varying(12),
    paid_partnership boolean DEFAULT false NOT NULL,
    made_with_ai boolean DEFAULT false NOT NULL
);


--
-- Name: age(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.age(public.comments) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT CAST( EXTRACT( EPOCH FROM CURRENT_TIMESTAMP) AS int) - $1.created_utc
      $_$;


--
-- Name: submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submissions (
    id integer NOT NULL,
    author_id integer,
    created_utc integer NOT NULL,
    is_banned boolean,
    distinguish_level integer,
    created_str character varying(255),
    stickied boolean,
    board_id integer,
    deleted_utc integer NOT NULL,
    domain_ref integer,
    is_approved integer NOT NULL,
    approved_utc integer,
    original_board_id integer,
    edited_utc integer,
    creation_ip character varying(64) NOT NULL,
    mod_approved integer,
    is_image boolean,
    has_thumb boolean,
    accepted_utc integer,
    post_public boolean,
    score_hot double precision,
    score_top integer,
    score_activity double precision,
    score_disputed double precision,
    is_offensive boolean,
    is_pinned boolean,
    is_sensitive boolean,
    repost_id integer,
    score_best double precision,
    upvotes integer,
    downvotes integer,
    gm_distinguish integer DEFAULT 0 NOT NULL,
    app_id integer,
    creation_region character(2) DEFAULT NULL::bpchar,
    purged_utc integer DEFAULT 0,
    is_bot boolean DEFAULT false,
    hidden_by_guild boolean DEFAULT false,
    language_code character varying(5) DEFAULT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    word_filter_version character varying(12),
    comment_permission smallint DEFAULT 0 NOT NULL,
    paid_partnership boolean DEFAULT false NOT NULL,
    made_with_ai boolean DEFAULT false NOT NULL
);


--
-- Name: age(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.age(public.submissions) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT CAST( EXTRACT( EPOCH FROM CURRENT_TIMESTAMP) AS int) - $1.created_utc
      $_$;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(255) NOT NULL,
    email character varying(255),
    passhash character varying(255) NOT NULL,
    created_utc integer NOT NULL,
    admin_level integer,
    creation_ip character varying(255),
    hide_offensive boolean,
    is_activated boolean,
    bio character varying(300),
    bio_html character varying(1000),
    real_id character varying,
    referred_by integer,
    is_banned integer,
    ban_reason character varying(128),
    login_nonce integer,
    title_id integer,
    has_banner boolean NOT NULL,
    has_profile boolean NOT NULL,
    reserved character varying(256),
    is_nsfw boolean NOT NULL,
    tos_agreed_utc integer,
    profile_nonce integer NOT NULL,
    banner_nonce integer NOT NULL,
    last_siege_utc integer,
    mfa_secret character varying(32),
    has_earned_darkmode boolean,
    is_private boolean,
    read_announcement_utc integer,
    feed_nonce integer,
    show_nsfl boolean,
    karma integer,
    comment_karma integer,
    unban_utc integer,
    is_deleted boolean,
    delete_reason character varying(1000),
    is_enrolled boolean,
    is_nofollow boolean DEFAULT false,
    coin_balance integer DEFAULT 0,
    premium_expires_utc integer DEFAULT 0,
    premium_first_purchased_utc integer DEFAULT 0,
    negative_balance_cents integer DEFAULT 0,
    display_region character varying(32),
    region_settled_utc integer DEFAULT 0,
    region_suspicion_flag boolean DEFAULT false,
    custom_filter_list character varying(1000) DEFAULT ''::character varying,
    discord_id character varying(64),
    last_yank_utc integer DEFAULT 0,
    stored_karma integer DEFAULT 0,
    stored_subscriber_count integer DEFAULT 0,
    creation_region character(2) DEFAULT NULL::bpchar,
    ban_evade integer DEFAULT 0,
    profile_upload_ip character varying(255),
    banner_upload_ip character varying(255),
    profile_upload_region character(2),
    banner_upload_region character(2),
    name_last_changed_utc integer,
    banner_set_utc integer DEFAULT 0,
    profile_set_utc integer DEFAULT 0,
    original_username character varying(255),
    name_changed_utc integer DEFAULT 0,
    hide_bot boolean DEFAULT false,
    auto_join_chat boolean DEFAULT true,
    last_mfa character(6),
    defaulttime character varying(8) DEFAULT 'all'::character varying,
    defaultsorting character varying(8) DEFAULT 'hot'::character varying,
    stored_follower_count integer DEFAULT 0,
    color character(6) DEFAULT '805ad5'::bpchar,
    secondary_color character(6) DEFAULT 'ffff00'::bpchar,
    signature character varying(280),
    signature_html character varying(512),
    filter_level smallint DEFAULT 1 NOT NULL,
    name_severity smallint DEFAULT 0 NOT NULL,
    bio_severity smallint DEFAULT 0 NOT NULL
);


--
-- Name: age(public.users); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.age(public.users) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT CAST( EXTRACT( EPOCH FROM CURRENT_TIMESTAMP) AS int) - $1.created_utc
      $_$;


--
-- Name: avg_score_computed(public.boards); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.avg_score_computed(public.boards) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
select coalesce (
	(select avg(score_top) from submissions
	where original_board_id=$1.id
	and score_top>0)
	,
	1
	)
$_$;


--
-- Name: board_id(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.board_id(public.comments) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT submissions.board_id
      FROM submissions
      WHERE submissions.id=$1.parent_submission
      $_$;


--
-- Name: reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reports (
    id integer NOT NULL,
    post_id integer,
    user_id integer,
    created_utc integer
);


--
-- Name: board_id(public.reports); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.board_id(public.reports) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT submissions.board_id
      FROM submissions
      WHERE submissions.id=$1.post_id
      $_$;


--
-- Name: comment_count(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.comment_count(public.submissions) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT COUNT(*)
      FROM comments
      WHERE is_banned=false
        AND deleted_utc=0
        AND parent_submission = $1.id
      $_$;


--
-- Name: comment_energy(public.users); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.comment_energy(public.users) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
     SELECT COALESCE(
     (
      SELECT SUM(comments.score_top)
      FROM comments
      WHERE comments.author_id=$1.id
        AND comments.is_banned=false
        and comments.parent_submission is not null
      ),
      0
      )
    $_$;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id integer NOT NULL,
    user_id integer,
    comment_id integer,
    read boolean NOT NULL,
    submission_id integer
);


--
-- Name: created_utc(public.notifications); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.created_utc(public.notifications) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
select created_utc from comments
where comments.id=$1.comment_id
$_$;


--
-- Name: downs(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.downs(public.comments) RETURNS bigint
    LANGUAGE sql
    AS $_$
select (
(
  SELECT count(*)
  from (
    select * from commentvotes
    where comment_id=$1.id
    and vote_type=-1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.original_board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where users.is_banned=0 or users.unban_utc>0
) as u0
    on u0.id=v1.user_id
)-(
  SELECT count(distinct v1.id)
  from (
    select * from commentvotes
    where comment_id=$1.id
    and vote_type=-1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.original_board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where is_banned=0 or users.unban_utc>0) as u1
    on u1.id=v1.user_id
   join (select * from alts) as a
    on (a.user1=v1.user_id or a.user2=v1.user_id)
   join (
      select * from commentvotes
      where comment_id=$1.id
      and vote_type=-1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.original_board_id
    	and is_active=true
    )
  ) as v2
    on ((a.user1=v2.user_id or a.user2=v2.user_id) and v2.id != v1.id)
   join (select * from users where is_banned=0 or users.unban_utc>0) as u2
    on u2.id=v2.user_id
  where v1.id is not null
  and v2.id is not null
))
     $_$;


--
-- Name: downs(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.downs(public.submissions) RETURNS bigint
    LANGUAGE sql
    AS $_$
select (
(
  SELECT count(*)
  from (
    select * from votes
    where submission_id=$1.id
    and vote_type=-1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where users.is_banned=0 or users.unban_utc>0) as u0
    on u0.id=v1.user_id
)-(
  SELECT count(distinct v1.id)
  from (
    select * from votes
    where submission_id=$1.id
    and vote_type=-1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where is_banned=0 or users.unban_utc>0) as u1
    on u1.id=v1.user_id
   join (select * from alts) as a
    on (a.user1=v1.user_id or a.user2=v1.user_id)
   join (
      select * from votes
      where submission_id=$1.id
      and vote_type=-1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.board_id
    	and is_active=true
    )
  ) as v2
    on ((a.user1=v2.user_id or a.user2=v2.user_id) and v2.id != v1.id)
   join (select * from users where is_banned=0 or users.unban_utc>0) as u2
    on u2.id=v2.user_id
  where v1.id is not null
  and v2.id is not null
))
     $_$;


--
-- Name: energy(public.users); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.energy(public.users) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
     SELECT COALESCE(
     (
      SELECT SUM(submissions.score_top)
      FROM submissions
      WHERE submissions.author_id=$1.id
        AND submissions.is_banned=false
        AND NOT (
          submissions.score_top < 0
          AND (
            EXISTS (
              SELECT 1 FROM forwardrels
              WHERE forwardrels.forward_submission_id = submissions.id
                AND forwardrels.forwarded_by_id <> submissions.author_id
            )
            OR EXISTS (
              SELECT 1 FROM comment_forwardrels
              WHERE comment_forwardrels.promoted_submission_id = submissions.id
                AND comment_forwardrels.promoted_by_id <> submissions.author_id
            )
          )
        )
      ),
      0
      )
    $_$;


--

-- Forwarder rep bonus/penalty: a fixed 25% bonus of a forward copy's
-- positive score, or the full negative score, credited to whoever
-- forwarded/promoted it (not the original author, who is excluded from
-- negative forward-copy scores by energy() above). Self-forwards (the
-- author forwarding their own content) are excluded here to match
-- energy()'s unchanged behavior for that case.

CREATE FUNCTION public.forward_bonus_energy(public.users) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
     SELECT COALESCE(SUM(bonus), 0)::bigint
     FROM (
       SELECT CASE WHEN s.score_top >= 0
                   THEN FLOOR(s.score_top * 0.25)
                   ELSE s.score_top
              END AS bonus
       FROM forwardrels fr
       JOIN submissions s ON s.id = fr.forward_submission_id
       WHERE fr.forwarded_by_id = $1.id
         AND fr.forwarded_by_id <> s.author_id
         AND s.is_banned = false

       UNION ALL

       SELECT CASE WHEN s.score_top >= 0
                   THEN FLOOR(s.score_top * 0.25)
                   ELSE s.score_top
              END AS bonus
       FROM comment_forwardrels cfr
       JOIN submissions s ON s.id = cfr.promoted_submission_id
       WHERE cfr.promoted_by_id = $1.id
         AND cfr.promoted_by_id <> s.author_id
         AND s.is_banned = false
     ) AS bonus_rows
    $_$;


--
-- Name: flag_count(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.flag_count(public.comments) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT COUNT(*)
      FROM commentflags
      JOIN users ON commentflags.user_id=users.id
      WHERE comment_id=$1.id
      AND users.is_banned=0
      $_$;


--
-- Name: flag_count(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.flag_count(public.submissions) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT COUNT(*)
      FROM flags
      JOIN users ON flags.user_id=users.id
      WHERE post_id=$1.id
      AND users.is_banned=0
      $_$;


--
-- Name: follower_count(public.users); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.follower_count(public.users) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
	select (
         (select count(*)
         from follows
         left join users
         on follows.user_id=users.id
         where follows.target_id=$1.id
         and (users.is_banned=0 or users.created_utc>0)
         and users.is_deleted=false
         )-(
	         select count(distinct f1.id)
	         	from
	         	(
	         		select *
	         		from follows
	         		where target_id=$1.id
	         	) as f1
   				join (select * from users where is_banned=0 or unban_utc>0) as u1
    			 on u1.id=f1.user_id
				join (select * from alts) as a
			     on (a.user1=f1.user_id or a.user2=f1.user_id)
			    join (
			    	select *
			    	from follows
			    	where target_id=$1.id
			    ) as f2
			    on ((a.user1=f2.user_id or a.user2=f2.user_id) and f2.id != f1.id)
			    join (select * from users where is_banned=0 or unban_utc>0) as u2
			     on u2.id=f2.user_id
			    where f1.id is not null
			    and f2.id is not null        	
	         )
         
         
         
         )
        $_$;


--
-- Name: is_banned(public.notifications); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.is_banned(public.notifications) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
select is_banned from comments
where comments.id=$1.comment_id
$_$;


--
-- Name: is_deleted(public.notifications); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.is_deleted(public.notifications) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
select is_deleted from comments
where comments.id=$1.comment_id
$_$;


--
-- Name: is_public(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.is_public(public.comments) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT submissions.is_public
      FROM submissions
      WHERE submissions.id=$1.parent_submission
      $_$;


--
-- Name: is_public(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.is_public(public.submissions) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
select
	case
		when $1.post_public=true
			then true
		when (select (is_private)
			from boards
			where id=$1.board_id
			)=true
			then false
		else
			true
	end
      
      
      $_$;


--
-- Name: mod_count(public.users); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.mod_count(public.users) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$select count(*) from mods where accepted=true and invite_rescinded=false and user_id=$1.id;$_$;


--
-- Name: rank_activity(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rank_activity(public.submissions) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT 1000000.0*CAST($1.comment_count AS float)/((CAST(($1.age+5000) AS FLOAT)/100.0)^(1.35))
    $_$;


--
-- Name: rank_best(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rank_best(public.submissions) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT 10000000.0*CAST(($1.upvotes - $1.downvotes + 1) AS float)/((CAST(($1.age+3600) AS FLOAT)*cast((select boards.subscriber_count from boards where boards.id=$1.board_id)+10000 as float)/1000.0)^(1.35))
      $_$;


--
-- Name: rank_fiery(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rank_fiery(public.comments) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT SQRT(CAST(($1.upvotes * $1.downvotes) AS float))/((CAST(($1.age+100000) AS FLOAT)/6.0)^(1.5))
  $_$;


--
-- Name: rank_fiery(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rank_fiery(public.submissions) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT 1000000.0*SQRT(CAST(($1.upvotes * $1.downvotes) AS float))/((CAST(($1.age+5000) AS FLOAT)/100.0)^(1.35))
      $_$;


--
-- Name: rank_hot(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rank_hot(public.comments) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CAST(($1.upvotes - $1.downvotes) AS float)/((CAST(($1.age+100000) AS FLOAT)/6.0)^(1.5))
  $_$;


--
-- Name: rank_hot(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rank_hot(public.submissions) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT 1000000.0*CAST(($1.upvotes - $1.downvotes) AS float)/((CAST(($1.age+5000) AS FLOAT)/100.0)^(1.5))
      $_$;


--
-- Name: recent_subscriptions(public.boards); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.recent_subscriptions(public.boards) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
         select count(*)
         from subscriptions
         left join users
         on subscriptions.user_id=users.id
         where subscriptions.board_id=$1.id
         and subscriptions.is_active=true
         and subscriptions.created_utc > CAST( EXTRACT( EPOCH FROM CURRENT_TIMESTAMP) AS int) - 60*60*24
         and users.is_banned=0
        $_$;


--
-- Name: referral_count(public.users); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.referral_count(public.users) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
        SELECT COUNT(*)
        FROM USERS
        WHERE users.is_banned=0
        AND users.referred_by=$1.id
    $_$;


--
-- Name: report_count(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.report_count(public.submissions) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT COUNT(*)
      FROM reports
      JOIN users ON reports.user_id=users.id
      WHERE post_id=$1.id
      AND users.is_banned=0
      and reports.created_utc >= $1.edited_utc
      $_$;


--
-- Name: score(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.score(public.comments) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT ($1.upvotes - $1.downvotes)
      $_$;


--
-- Name: score(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.score(public.submissions) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT ($1.upvotes - $1.downvotes)
      $_$;


--
-- Name: similar_count(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.similar_count(public.comments) RETURNS bigint
    LANGUAGE sql
    AS $_$ select count(*) from comments where author_id=$1.id and similarity(comments.body, $1.body) > 0.5 $_$;


--
-- Name: images; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.images (
    id integer NOT NULL,
    state character varying(8),
    text character varying(255),
    number integer
);


--
-- Name: splash(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.splash(text) RETURNS SETOF public.images
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT *
      FROM images
      WHERE state=$1
      ORDER BY random()
      LIMIT 1
    $_$;


--
-- Name: subscriber_count(public.boards); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.subscriber_count(public.boards) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$

	select
		case 
		when $1.is_private=false
		then
	         (
	         (
		         select count(*)
		         from subscriptions
		         left join users
		         on subscriptions.user_id=users.id
		         where subscriptions.board_id=$1.id
		         and subscriptions.is_active=true
		         and users.is_deleted=false and (users.is_banned=0 or users.unban_utc>0)
	         )-(
	         	select count(distinct s1.id)
	         	from
	         	(
	         		select *
	         		from subscriptions
	         		where board_id=$1.id
	         		and is_active=true
	         	) as s1
   				join (select * from users where is_banned=0 or unban_utc>0) as u1
    			 on u1.id=s1.user_id
				join (select * from alts) as a
			     on (a.user1=s1.user_id or a.user2=s1.user_id)
			    join (
			    	select *
			    	from subscriptions
			    	where board_id=$1.id
			    	and is_active=true
			    ) as s2
			    on ((a.user1=s2.user_id or a.user2=s2.user_id) and s2.id != s1.id)
			    join (select * from users where is_banned=0 or unban_utc>0) as u2
			     on u2.id=s2.user_id
			    where s1.id is not null
			    and s2.id is not null        	
	         )
	         )
	    when $1.is_private=true
	    then
	         (
	         (
	         select count(*)
	         from subscriptions
	         left join users
	         	on subscriptions.user_id=users.id
	         left join (
	         	select * from contributors
	         	where contributors.board_id=$1.id
	         )as contribs
	         	on contribs.user_id=users.id
	         left join (
	         	select * from mods
	         	where mods.board_id=$1.id
	         	and accepted=true
	         )as m
	         	on m.user_id=users.id
	         where subscriptions.board_id=$1.id
	         and subscriptions.is_active=true
	         and users.is_deleted=false and (users.is_banned=0 or users.unban_utc>0)
	         and (contribs.user_id is not null or m.id is not null)
	         )
	         )
	    end
         
         
$_$;


--
-- Name: trending_rank(public.boards); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trending_rank(public.boards) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$

select
	case 
		when $1.subscriber_count<=10 then 0
		when $1.age < 60*60*24*5 then 0
		when $1.recent_subscriptions<=5 then 0
		when $1.subscriber_count>=9 then ((cast($1.subscriber_count as float))^(1/3) + cast($1.recent_subscriptions as float)) / cast($1.subscriber_count + 10000 as float)
	end
$_$;


--
-- Name: ups(public.comments); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ups(public.comments) RETURNS bigint
    LANGUAGE sql
    AS $_$
select (
(
  SELECT count(*)
  from (
    select * from commentvotes
    where comment_id=$1.id
    and vote_type=1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.original_board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where users.is_banned=0 or users.unban_utc>0) as u0
    on u0.id=v1.user_id
)-(
  SELECT count(distinct v1.id)
  from (
    select * from commentvotes
    where comment_id=$1.id
    and vote_type=1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.original_board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where is_banned=0 or users.unban_utc>0) as u1
    on u1.id=v1.user_id
   join (select * from alts) as a
    on (a.user1=v1.user_id or a.user2=v1.user_id)
   join (
      select * from commentvotes
      where comment_id=$1.id
      and vote_type=1
	    and user_id not in
	    (
	    	select user_id
	    	from bans
	    	where board_id=$1.original_board_id
    		and is_active=true
	    )
  ) as v2
    on ((a.user1=v2.user_id or a.user2=v2.user_id) and v2.id != v1.id)
   join (select * from users where is_banned=0 or users.unban_utc>0) as u2
    on u2.id=v2.user_id
  where v1.id is not null
  and v2.id is not null
))
     $_$;


--
-- Name: ups(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ups(public.submissions) RETURNS bigint
    LANGUAGE sql
    AS $_$
select (
(
  SELECT count(*)
  from (
    select * from votes
    where submission_id=$1.id
    and vote_type=1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where users.is_banned=0 or users.unban_utc>0) as u0
    on u0.id=v1.user_id
)-(
  SELECT count(distinct v1.id)
  from (
    select * from votes
    where submission_id=$1.id
    and vote_type=1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.board_id
    	and is_active=true
    )
  ) as v1
   join (select * from users where is_banned=0 or users.unban_utc>0) as u1
    on u1.id=v1.user_id
   join (select * from alts) as a
    on (a.user1=v1.user_id or a.user2=v1.user_id)
   join (
      select * from votes
      where submission_id=$1.id
      and vote_type=1
    and user_id not in
    (
    	select user_id
    	from bans
    	where board_id=$1.board_id
    	and is_active=true
    )
  ) as v2
    on ((a.user1=v2.user_id or a.user2=v2.user_id) and v2.id != v1.id)
   join (select * from users where is_banned=0 or users.unban_utc>0) as u2
    on u2.id=v2.user_id
  where v1.id is not null
  and v2.id is not null
))
     $_$;


--
-- Name: ups_test(public.submissions); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ups_test(public.submissions) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
select (
(
  SELECT count(*)
  from (
    select * from votes
    where submission_id=$1.id
    and vote_type=1
  ) as v1
   join (select * from users where users.is_banned=0) as u0
    on u0.id=v1.user_id
)-(
  SELECT count(distinct v1.id)
  from (
    select * from votes
    where submission_id=$1.id
    and vote_type=1
  ) as v1
   join (select * from users where is_banned=0) as u1
    on u1.id=v1.user_id
   join (select * from alts) as a
    on (a.user1=v1.user_id or a.user2=v1.user_id)
   join (
      select * from votes
      where submission_id=$1.id
      and vote_type=1
  ) as v2
    on ((a.user1=v2.id or a.user2=v2.id) and v2.id != v1.id)
   join (select * from users where is_banned=0) as u2
    on u2.id=v2.user_id
  where v1.id is not null
  and v2.id is not null
))
      $_$;


--
-- Name: alts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alts (
    id integer NOT NULL,
    user1 integer NOT NULL,
    user2 integer NOT NULL,
    is_manual boolean DEFAULT false
);


--
-- Name: alts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alts_id_seq OWNED BY public.alts.id;


--
-- Name: award_relationships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.award_relationships (
    id integer NOT NULL,
    user_id integer,
    submission_id integer,
    comment_id integer
);


--
-- Name: award_relationships_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.award_relationships_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: award_relationships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.award_relationships_id_seq OWNED BY public.award_relationships.id;


--
-- Name: badge_defs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badge_defs (
    id integer NOT NULL,
    name character varying(64),
    description character varying(256),
    icon character varying(64),
    kind integer,
    rank integer,
    qualification_expr character varying(128)
);


--
-- Name: badge_list_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.badge_list_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: badge_list_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.badge_list_id_seq OWNED BY public.badge_defs.id;


--
-- Name: badges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badges (
    id integer NOT NULL,
    badge_id integer,
    user_id integer,
    description character varying(256),
    url character varying(256),
    created_utc integer
);


--
-- Name: badges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.badges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: badges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.badges_id_seq OWNED BY public.badges.id;


--
-- Name: badlinks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badlinks (
    id integer NOT NULL,
    reason integer,
    link character varying(512),
    autoban boolean
);


--
-- Name: badlinks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.badlinks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: badlinks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.badlinks_id_seq OWNED BY public.badlinks.id;


--
-- Name: badpics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badpics (
    id integer NOT NULL,
    description character varying(255),
    phash character varying(64),
    ban_reason character varying(64),
    ban_time integer DEFAULT 0 NOT NULL
);


--
-- Name: badpics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.badpics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: badpics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.badpics_id_seq OWNED BY public.badpics.id;


--
-- Name: badwords; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.badwords (
    id integer NOT NULL,
    keyword character varying(64),
    regex character varying(256)
);


--
-- Name: badwords_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.badwords_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: badwords_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.badwords_id_seq OWNED BY public.badwords.id;


--
-- Name: bans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bans (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    created_utc integer,
    banning_mod_id integer,
    is_active boolean NOT NULL,
    mod_note character varying(128)
);


--
-- Name: bans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bans_id_seq OWNED BY public.bans.id;


--
-- Name: boardblocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.boardblocks (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    created_utc integer
);


--
-- Name: boardblocks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.boardblocks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: boardblocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.boardblocks_id_seq OWNED BY public.boardblocks.id;


--
-- Name: boards_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.boards_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: boards_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.boards_id_seq OWNED BY public.boards.id;


--
-- Name: categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.categories (
    id integer NOT NULL,
    color character(6) DEFAULT '805ad5'::bpchar,
    visible boolean DEFAULT true,
    name character varying(64),
    description character varying(512),
    icon character varying(64) DEFAULT NULL::character varying,
    is_nsfw boolean DEFAULT false
);


--
-- Name: categories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.categories_id_seq OWNED BY public.categories.id;


--
-- Name: chatbans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chatbans (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    created_utc integer,
    banning_mod_id integer
);


--
-- Name: chatbans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chatbans_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chatbans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chatbans_id_seq OWNED BY public.chatbans.id;


--
-- Name: client_auths; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client_auths (
    id integer NOT NULL,
    user_id integer,
    oauth_client integer,
    scope_identity boolean,
    scope_create boolean,
    scope_read boolean,
    scope_update boolean,
    scope_delete boolean,
    scope_vote boolean,
    scope_guildmaster boolean,
    access_token character(128),
    refresh_token character(128),
    oauth_code character(128),
    access_token_expire_utc integer
);


--
-- Name: client_auths_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.client_auths_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: client_auths_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.client_auths_id_seq OWNED BY public.client_auths.id;


--
-- Name: commentflags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commentflags (
    id integer NOT NULL,
    user_id integer,
    comment_id integer,
    created_utc integer NOT NULL
);


--
-- Name: commentflags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.commentflags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: commentflags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.commentflags_id_seq OWNED BY public.commentflags.id;


--
-- Name: comments_aux; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comments_aux (
    id integer,
    body character varying(10000),
    body_html character varying(20000),
    ban_reason character varying(128),
    hidden_reason character varying(256) DEFAULT ''::character varying,
    author_hidden_reason character varying(256) DEFAULT ''::character varying,
    key_id integer NOT NULL
);


--
-- Name: comments_aux_key_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comments_aux_key_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comments_aux_key_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comments_aux_key_id_seq OWNED BY public.comments_aux.key_id;


--
-- Name: comments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comments_id_seq OWNED BY public.comments.id;


--
-- Name: commentvotes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commentvotes (
    id integer NOT NULL,
    comment_id integer,
    vote_type integer,
    user_id integer,
    created_utc integer,
    creation_ip character(64),
    app_id integer
);


--
-- Name: commentvotes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.commentvotes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: commentvotes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.commentvotes_id_seq OWNED BY public.commentvotes.id;


--
-- Name: contributors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contributors (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    created_utc integer,
    is_active boolean,
    approving_mod_id integer
);


--
-- Name: contributors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contributors_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contributors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contributors_id_seq OWNED BY public.contributors.id;


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id integer NOT NULL,
    author_id integer NOT NULL,
    created_utc integer,
    subject character(256),
    board_id integer
);


--
-- Name: conversations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversations_id_seq OWNED BY public.conversations.id;


--
-- Name: convo_member; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.convo_member (
    id integer NOT NULL,
    user_id integer,
    convo_id integer
);


--
-- Name: convo_member_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.convo_member_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: convo_member_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.convo_member_id_seq OWNED BY public.convo_member.id;


--
-- Name: dms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dms (
    id integer NOT NULL,
    created_utc integer,
    to_user_id integer,
    from_user_id integer,
    body_html character varying(300),
    is_banned boolean
);


--
-- Name: dms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dms_id_seq OWNED BY public.dms.id;


--
-- Name: domains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.domains (
    id integer NOT NULL,
    domain character varying(100),
    can_submit boolean,
    can_comment boolean,
    reason integer,
    show_thumbnail boolean,
    embed_function character varying(64),
    embed_template character varying(32) DEFAULT NULL::character varying,
    sandbox_embed boolean DEFAULT false
);


--
-- Name: domains_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.domains_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: domains_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.domains_id_seq OWNED BY public.domains.id;


--
-- Name: flags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flags (
    id integer NOT NULL,
    user_id integer,
    post_id integer,
    created_utc integer NOT NULL
);


--
-- Name: flags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.flags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: flags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.flags_id_seq OWNED BY public.flags.id;


--
-- Name: follows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.follows (
    id integer NOT NULL,
    user_id integer,
    target_id integer,
    created_utc integer,
    get_notifs boolean DEFAULT false
);


--
-- Name: follows_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.follows_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: follows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.follows_id_seq OWNED BY public.follows.id;


--
-- Name: images_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.images_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: images_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.images_id_seq OWNED BY public.images.id;


--
-- Name: ips; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ips (
    id integer NOT NULL,
    addr character varying(64),
    reason character varying(256),
    banned_by integer
);


--
-- Name: ips_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ips_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ips_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ips_id_seq OWNED BY public.ips.id;


--
-- Name: lodges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lodges (
    id integer NOT NULL,
    created_utc integer,
    board_id integer,
    name character varying(32)
);


--
-- Name: lodges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.lodges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: lodges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.lodges_id_seq OWNED BY public.lodges.id;


--
-- Name: message_notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.message_notifications (
    id integer NOT NULL,
    user_id integer,
    message_id integer,
    has_read boolean DEFAULT false
);


--
-- Name: message_notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.message_notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: message_notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.message_notifications_id_seq OWNED BY public.message_notifications.id;


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id integer NOT NULL,
    author_id integer NOT NULL,
    body character(10000),
    body_html character(15000),
    created_utc integer,
    distinguish_level integer DEFAULT 0,
    convo_id integer
);


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: modactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.modactions (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    target_user_id integer,
    target_submission_id integer,
    target_comment_id integer,
    created_utc integer DEFAULT 0,
    kind character varying(32) DEFAULT NULL::character varying,
    _note character varying(256) DEFAULT NULL::character varying
);


--
-- Name: modactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.modactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: modactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.modactions_id_seq OWNED BY public.modactions.id;


--
-- Name: mods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mods (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    created_utc integer,
    accepted boolean,
    invite_rescinded boolean,
    perm_full boolean DEFAULT true,
    perm_content boolean DEFAULT true,
    perm_appearance boolean DEFAULT true,
    perm_access boolean DEFAULT true,
    perm_config boolean DEFAULT true,
    perm_chat boolean DEFAULT false
);


--
-- Name: mods_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mods_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mods_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mods_id_seq OWNED BY public.mods.id;


--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: oauth_apps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oauth_apps (
    id integer NOT NULL,
    client_id character(64),
    client_secret character(128),
    app_name character varying(50),
    redirect_uri character varying(4096),
    author_id integer,
    is_banned boolean,
    description character varying(256)
);


--
-- Name: oauth_apps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.oauth_apps_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: oauth_apps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.oauth_apps_id_seq OWNED BY public.oauth_apps.id;


--
-- Name: paypal_txns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.paypal_txns (
    id integer NOT NULL,
    user_id integer,
    created_utc integer,
    paypal_id character varying(64),
    usd_cents integer,
    status integer DEFAULT 0,
    coin_count integer DEFAULT 1 NOT NULL,
    promo_id integer,
    payer_country character(2)
);


--
-- Name: paypal_txns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.paypal_txns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paypal_txns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.paypal_txns_id_seq OWNED BY public.paypal_txns.id;


--
-- Name: postrels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.postrels (
    id integer NOT NULL,
    post_id integer,
    board_id integer
);


--
-- Name: postrels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.postrels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: postrels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.postrels_id_seq OWNED BY public.postrels.id;


--
-- Name: forwardrels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.forwardrels (
    id integer NOT NULL,
    primary_submission_id integer,
    board_id integer,
    forward_submission_id integer,
    forwarded_by_id integer,
    created_utc integer DEFAULT 0
);


--
-- Name: forwardrels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.forwardrels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: forwardrels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.forwardrels_id_seq OWNED BY public.forwardrels.id;


--
-- Name: comment_forwardrels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comment_forwardrels (
    id bigint NOT NULL,
    comment_id integer,
    board_id integer,
    promoted_submission_id integer,
    promoted_by_id integer,
    created_utc integer DEFAULT 0
);


--
-- Name: comment_forwardrels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comment_forwardrels_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comment_forwardrels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comment_forwardrels_id_seq OWNED BY public.comment_forwardrels.id;


--
-- Name: promocodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.promocodes (
    id integer NOT NULL,
    code character varying(64) NOT NULL,
    is_active boolean DEFAULT false,
    percent_off integer,
    flat_cents_off integer,
    flat_cents_min integer,
    promo_start_utc integer,
    promo_end_utc integer,
    promo_info character varying(64) DEFAULT NULL::character varying
);


--
-- Name: promocodes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.promocodes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: promocodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.promocodes_id_seq OWNED BY public.promocodes.id;


--
-- Name: reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reports_id_seq OWNED BY public.reports.id;


--
-- Name: save_relationship; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.save_relationship (
    id integer NOT NULL,
    submission_id integer,
    user_id integer,
    created_utc integer DEFAULT 0
);


--
-- Name: save_relationship_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.save_relationship_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: save_relationship_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.save_relationship_id_seq OWNED BY public.save_relationship.id;


--
-- Name: repost_relationship; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.repost_relationship (
    id integer NOT NULL,
    user_id integer,
    submission_id integer,
    created_utc integer DEFAULT 0
);


--
-- Name: repost_relationship_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.repost_relationship_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: repost_relationship_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.repost_relationship_id_seq OWNED BY public.repost_relationship.id;


--
-- Name: comment_save_relationship; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comment_save_relationship (
    id integer NOT NULL,
    user_id integer,
    comment_id integer,
    created_utc integer DEFAULT 0
);


--
-- Name: comment_save_relationship_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comment_save_relationship_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comment_save_relationship_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comment_save_relationship_id_seq OWNED BY public.comment_save_relationship.id;


--
-- Name: comment_repost_relationship; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comment_repost_relationship (
    id integer NOT NULL,
    user_id integer,
    comment_id integer,
    created_utc integer DEFAULT 0
);


--
-- Name: comment_repost_relationship_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.comment_repost_relationship_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: comment_repost_relationship_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.comment_repost_relationship_id_seq OWNED BY public.comment_repost_relationship.id;


--
-- Name: view_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.view_history (
    id integer NOT NULL,
    user_id integer,
    submission_id integer,
    viewed_utc integer
);


--
-- Name: view_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.view_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: view_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.view_history_id_seq OWNED BY public.view_history.id;


--
-- Name: subcategories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subcategories (
    id integer NOT NULL,
    cat_id integer,
    name character varying(64),
    description character varying(512),
    _visible boolean
);


--
-- Name: subcategories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subcategories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subcategories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subcategories_id_seq OWNED BY public.subcategories.id;


--
-- Name: submissions_aux; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submissions_aux (
    id integer,
    title character varying(500),
    url character varying(2083),
    body character varying(25000),
    body_html character varying(50000),
    embed_url character varying(10000),
    embed_type character varying(16),
    ban_reason character varying(128),
    key_id integer NOT NULL,
    meta_title character varying(512),
    meta_description character varying(1024),
    preview_image_url character varying(1024) DEFAULT ''::character varying,
    hidden_reason character varying(128) DEFAULT ''::character varying
);


--
-- Name: submissions_aux_key_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.submissions_aux_key_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: submissions_aux_key_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.submissions_aux_key_id_seq OWNED BY public.submissions_aux.key_id;


--
-- Name: submissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.submissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: submissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.submissions_id_seq OWNED BY public.submissions.id;


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    id integer NOT NULL,
    user_id integer,
    board_id integer,
    created_utc integer NOT NULL,
    is_active boolean,
    get_notifs boolean DEFAULT false
);


--
-- Name: subscriptions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subscriptions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subscriptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subscriptions_id_seq OWNED BY public.subscriptions.id;


--
-- Name: titles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.titles (
    id integer NOT NULL,
    is_before boolean NOT NULL,
    text character varying(64),
    qualification_expr character varying(256),
    requirement_string character varying(512),
    color character varying(6),
    kind integer,
    background_color_1 character varying(8),
    background_color_2 character varying(8),
    gradient_angle integer,
    box_shadow_color character varying(32),
    text_shadow_color character varying(32)
);


--
-- Name: titles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.titles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: titles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.titles_id_seq OWNED BY public.titles.id;


--
-- Name: useragents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.useragents (
    id integer NOT NULL,
    kwd character varying(128),
    banned_by integer,
    reason character varying(256),
    mock character varying(256),
    status_code integer
);


--
-- Name: useragents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.useragents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: useragents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.useragents_id_seq OWNED BY public.useragents.id;


--
-- Name: userblocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.userblocks (
    id integer NOT NULL,
    user_id integer,
    target_id integer,
    created_utc integer
);


--
-- Name: userblocks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.userblocks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: userblocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.userblocks_id_seq OWNED BY public.userblocks.id;


--
-- Name: userflags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.userflags (
    id integer NOT NULL,
    user_id integer,
    target_id integer,
    resolved boolean
);


--
-- Name: userflags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.userflags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: userflags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.userflags_id_seq OWNED BY public.userflags.id;


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.votes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    submission_id integer,
    created_utc integer NOT NULL,
    vote_type integer,
    creation_ip character(64),
    app_id integer
);


--
-- Name: votes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.votes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: votes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.votes_id_seq OWNED BY public.votes.id;


--
-- Name: alts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alts ALTER COLUMN id SET DEFAULT nextval('public.alts_id_seq'::regclass);


--
-- Name: award_relationships id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.award_relationships ALTER COLUMN id SET DEFAULT nextval('public.award_relationships_id_seq'::regclass);


--
-- Name: badge_defs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badge_defs ALTER COLUMN id SET DEFAULT nextval('public.badge_list_id_seq'::regclass);


--
-- Name: badges id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges ALTER COLUMN id SET DEFAULT nextval('public.badges_id_seq'::regclass);


--
-- Name: badlinks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badlinks ALTER COLUMN id SET DEFAULT nextval('public.badlinks_id_seq'::regclass);


--
-- Name: badpics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badpics ALTER COLUMN id SET DEFAULT nextval('public.badpics_id_seq'::regclass);


--
-- Name: badwords id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badwords ALTER COLUMN id SET DEFAULT nextval('public.badwords_id_seq'::regclass);


--
-- Name: bans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bans ALTER COLUMN id SET DEFAULT nextval('public.bans_id_seq'::regclass);


--
-- Name: boardblocks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boardblocks ALTER COLUMN id SET DEFAULT nextval('public.boardblocks_id_seq'::regclass);


--
-- Name: boards id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boards ALTER COLUMN id SET DEFAULT nextval('public.boards_id_seq'::regclass);


--
-- Name: categories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories ALTER COLUMN id SET DEFAULT nextval('public.categories_id_seq'::regclass);


--
-- Name: chatbans id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chatbans ALTER COLUMN id SET DEFAULT nextval('public.chatbans_id_seq'::regclass);


--
-- Name: client_auths id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_auths ALTER COLUMN id SET DEFAULT nextval('public.client_auths_id_seq'::regclass);


--
-- Name: commentflags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commentflags ALTER COLUMN id SET DEFAULT nextval('public.commentflags_id_seq'::regclass);


--
-- Name: comments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments ALTER COLUMN id SET DEFAULT nextval('public.comments_id_seq'::regclass);


--
-- Name: comments_aux key_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments_aux ALTER COLUMN key_id SET DEFAULT nextval('public.comments_aux_key_id_seq'::regclass);


--
-- Name: commentvotes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commentvotes ALTER COLUMN id SET DEFAULT nextval('public.commentvotes_id_seq'::regclass);


--
-- Name: contributors id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contributors ALTER COLUMN id SET DEFAULT nextval('public.contributors_id_seq'::regclass);


--
-- Name: conversations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations ALTER COLUMN id SET DEFAULT nextval('public.conversations_id_seq'::regclass);


--
-- Name: convo_member id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.convo_member ALTER COLUMN id SET DEFAULT nextval('public.convo_member_id_seq'::regclass);


--
-- Name: dms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dms ALTER COLUMN id SET DEFAULT nextval('public.dms_id_seq'::regclass);


--
-- Name: domains id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domains ALTER COLUMN id SET DEFAULT nextval('public.domains_id_seq'::regclass);


--
-- Name: flags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flags ALTER COLUMN id SET DEFAULT nextval('public.flags_id_seq'::regclass);


--
-- Name: follows id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follows ALTER COLUMN id SET DEFAULT nextval('public.follows_id_seq'::regclass);


--
-- Name: images id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.images ALTER COLUMN id SET DEFAULT nextval('public.images_id_seq'::regclass);


--
-- Name: ips id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ips ALTER COLUMN id SET DEFAULT nextval('public.ips_id_seq'::regclass);


--
-- Name: lodges id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lodges ALTER COLUMN id SET DEFAULT nextval('public.lodges_id_seq'::regclass);


--
-- Name: message_notifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_notifications ALTER COLUMN id SET DEFAULT nextval('public.message_notifications_id_seq'::regclass);


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: modactions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.modactions ALTER COLUMN id SET DEFAULT nextval('public.modactions_id_seq'::regclass);


--
-- Name: mods id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mods ALTER COLUMN id SET DEFAULT nextval('public.mods_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: oauth_apps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_apps ALTER COLUMN id SET DEFAULT nextval('public.oauth_apps_id_seq'::regclass);


--
-- Name: paypal_txns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paypal_txns ALTER COLUMN id SET DEFAULT nextval('public.paypal_txns_id_seq'::regclass);


--
-- Name: postrels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postrels ALTER COLUMN id SET DEFAULT nextval('public.postrels_id_seq'::regclass);


--
-- Name: forwardrels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forwardrels ALTER COLUMN id SET DEFAULT nextval('public.forwardrels_id_seq'::regclass);


--
-- Name: comment_forwardrels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_forwardrels ALTER COLUMN id SET DEFAULT nextval('public.comment_forwardrels_id_seq'::regclass);


--
-- Name: promocodes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.promocodes ALTER COLUMN id SET DEFAULT nextval('public.promocodes_id_seq'::regclass);


--
-- Name: reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports ALTER COLUMN id SET DEFAULT nextval('public.reports_id_seq'::regclass);


--
-- Name: save_relationship id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.save_relationship ALTER COLUMN id SET DEFAULT nextval('public.save_relationship_id_seq'::regclass);


--
-- Name: repost_relationship id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repost_relationship ALTER COLUMN id SET DEFAULT nextval('public.repost_relationship_id_seq'::regclass);


--
-- Name: comment_save_relationship id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_save_relationship ALTER COLUMN id SET DEFAULT nextval('public.comment_save_relationship_id_seq'::regclass);


--
-- Name: comment_repost_relationship id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_repost_relationship ALTER COLUMN id SET DEFAULT nextval('public.comment_repost_relationship_id_seq'::regclass);


--
-- Name: view_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.view_history ALTER COLUMN id SET DEFAULT nextval('public.view_history_id_seq'::regclass);


--
-- Name: subcategories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subcategories ALTER COLUMN id SET DEFAULT nextval('public.subcategories_id_seq'::regclass);


--
-- Name: submissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions ALTER COLUMN id SET DEFAULT nextval('public.submissions_id_seq'::regclass);


--
-- Name: submissions_aux key_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions_aux ALTER COLUMN key_id SET DEFAULT nextval('public.submissions_aux_key_id_seq'::regclass);


--
-- Name: subscriptions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions ALTER COLUMN id SET DEFAULT nextval('public.subscriptions_id_seq'::regclass);


--
-- Name: titles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.titles ALTER COLUMN id SET DEFAULT nextval('public.titles_id_seq'::regclass);


--
-- Name: useragents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.useragents ALTER COLUMN id SET DEFAULT nextval('public.useragents_id_seq'::regclass);


--
-- Name: userblocks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.userblocks ALTER COLUMN id SET DEFAULT nextval('public.userblocks_id_seq'::regclass);


--
-- Name: userflags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.userflags ALTER COLUMN id SET DEFAULT nextval('public.userflags_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: votes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes ALTER COLUMN id SET DEFAULT nextval('public.votes_id_seq'::regclass);


--
-- Name: alts alts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alts
    ADD CONSTRAINT alts_pkey PRIMARY KEY (user1, user2);


--
-- Name: award_relationships award_comment_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.award_relationships
    ADD CONSTRAINT award_comment_constraint UNIQUE (user_id, comment_id);


--
-- Name: award_relationships award_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.award_relationships
    ADD CONSTRAINT award_constraint UNIQUE (user_id, submission_id, comment_id);


--
-- Name: award_relationships award_post_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.award_relationships
    ADD CONSTRAINT award_post_constraint UNIQUE (user_id, submission_id);


--
-- Name: award_relationships award_relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.award_relationships
    ADD CONSTRAINT award_relationships_pkey PRIMARY KEY (id);


--
-- Name: badge_defs badge_defs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badge_defs
    ADD CONSTRAINT badge_defs_pkey PRIMARY KEY (id);


--
-- Name: badge_defs badge_list_icon_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badge_defs
    ADD CONSTRAINT badge_list_icon_key UNIQUE (icon);


--
-- Name: badges badges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges
    ADD CONSTRAINT badges_pkey PRIMARY KEY (id);


--
-- Name: badlinks badlinks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badlinks
    ADD CONSTRAINT badlinks_pkey PRIMARY KEY (id);


--
-- Name: badpics badpics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badpics
    ADD CONSTRAINT badpics_pkey PRIMARY KEY (id);


--
-- Name: badwords badwords_keyword_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badwords
    ADD CONSTRAINT badwords_keyword_key UNIQUE (keyword);


--
-- Name: badwords badwords_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badwords
    ADD CONSTRAINT badwords_pkey PRIMARY KEY (id);


--
-- Name: bans bans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bans
    ADD CONSTRAINT bans_pkey PRIMARY KEY (id);


--
-- Name: boardblocks boardblocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boardblocks
    ADD CONSTRAINT boardblocks_pkey PRIMARY KEY (id);


--
-- Name: boards boards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boards
    ADD CONSTRAINT boards_pkey PRIMARY KEY (id);


--
-- Name: categories categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);


--
-- Name: chatbans chatbans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chatbans
    ADD CONSTRAINT chatbans_pkey PRIMARY KEY (id);


--
-- Name: client_auths client_auths_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_auths
    ADD CONSTRAINT client_auths_pkey PRIMARY KEY (id);


--
-- Name: commentflags commentflags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commentflags
    ADD CONSTRAINT commentflags_pkey PRIMARY KEY (id);


--
-- Name: comments_aux comments_aux_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments_aux
    ADD CONSTRAINT comments_aux_pkey PRIMARY KEY (key_id);


--
-- Name: comments comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT comments_pkey PRIMARY KEY (id);


--
-- Name: commentvotes commentvotes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commentvotes
    ADD CONSTRAINT commentvotes_pkey PRIMARY KEY (id);


--
-- Name: contributors contribs_unique_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contributors
    ADD CONSTRAINT contribs_unique_constraint UNIQUE (user_id, board_id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: convo_member convo_member_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.convo_member
    ADD CONSTRAINT convo_member_pkey PRIMARY KEY (id);


--
-- Name: dms dms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dms
    ADD CONSTRAINT dms_pkey PRIMARY KEY (id);


--
-- Name: domains domains_domain_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domains
    ADD CONSTRAINT domains_domain_key UNIQUE (domain);


--
-- Name: domains domains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.domains
    ADD CONSTRAINT domains_pkey PRIMARY KEY (id);


--
-- Name: flags flags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flags
    ADD CONSTRAINT flags_pkey PRIMARY KEY (id);


--
-- Name: follows follow_membership_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follows
    ADD CONSTRAINT follow_membership_unique UNIQUE (user_id, target_id);


--
-- Name: follows follows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follows
    ADD CONSTRAINT follows_pkey PRIMARY KEY (id);


--
-- Name: subscriptions guild_membership_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT guild_membership_unique UNIQUE (user_id, board_id);


--
-- Name: boards guild_names_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boards
    ADD CONSTRAINT guild_names_unique UNIQUE (name);


--
-- Name: contributors id_const; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contributors
    ADD CONSTRAINT id_const UNIQUE (id);


--
-- Name: images images_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.images
    ADD CONSTRAINT images_pkey PRIMARY KEY (id);


--
-- Name: ips ips_addr_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ips
    ADD CONSTRAINT ips_addr_key UNIQUE (addr);


--
-- Name: ips ips_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ips
    ADD CONSTRAINT ips_pkey PRIMARY KEY (id);


--
-- Name: lodges lodge_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lodges
    ADD CONSTRAINT lodge_constraint UNIQUE (board_id, name);


--
-- Name: lodges lodges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lodges
    ADD CONSTRAINT lodges_pkey PRIMARY KEY (id);


--
-- Name: message_notifications message_notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_notifications
    ADD CONSTRAINT message_notifications_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: mods mod_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mods
    ADD CONSTRAINT mod_unique UNIQUE (user_id, board_id);


--
-- Name: modactions modactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.modactions
    ADD CONSTRAINT modactions_pkey PRIMARY KEY (id);


--
-- Name: mods mods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mods
    ADD CONSTRAINT mods_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: oauth_apps oauth_apps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_apps
    ADD CONSTRAINT oauth_apps_pkey PRIMARY KEY (id);


--
-- Name: boardblocks one_board_block; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boardblocks
    ADD CONSTRAINT one_board_block UNIQUE (user_id, board_id);


--
-- Name: users one_discord_account; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT one_discord_account UNIQUE (discord_id);


--
-- Name: notifications one_notif; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT one_notif UNIQUE (user_id, comment_id);


--
-- Name: commentvotes onecvote; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commentvotes
    ADD CONSTRAINT onecvote UNIQUE (user_id, comment_id);


--
-- Name: votes onevote; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT onevote UNIQUE (user_id, submission_id);


--
-- Name: paypal_txns paypal_txns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paypal_txns
    ADD CONSTRAINT paypal_txns_pkey PRIMARY KEY (id);


--
-- Name: postrels postrel_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postrels
    ADD CONSTRAINT postrel_unique UNIQUE (post_id, board_id);


--
-- Name: postrels postrels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postrels
    ADD CONSTRAINT postrels_pkey PRIMARY KEY (id);


--
-- Name: forwardrels forward_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forwardrels
    ADD CONSTRAINT forward_unique UNIQUE (primary_submission_id, board_id);


--
-- Name: forwardrels forwardrels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forwardrels
    ADD CONSTRAINT forwardrels_pkey PRIMARY KEY (id);


--
-- Name: comment_forwardrels comment_forward_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_forwardrels
    ADD CONSTRAINT comment_forward_unique UNIQUE (comment_id, board_id);


--
-- Name: comment_forwardrels comment_forwardrels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_forwardrels
    ADD CONSTRAINT comment_forwardrels_pkey PRIMARY KEY (id);


--
-- Name: promocodes promocodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.promocodes
    ADD CONSTRAINT promocodes_pkey PRIMARY KEY (id);


--
-- Name: reports reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_pkey PRIMARY KEY (id);


--
-- Name: save_relationship save_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.save_relationship
    ADD CONSTRAINT save_constraint UNIQUE (submission_id, user_id);


--
-- Name: save_relationship save_relationship_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.save_relationship
    ADD CONSTRAINT save_relationship_pkey PRIMARY KEY (id);


--
-- Name: repost_relationship repost_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repost_relationship
    ADD CONSTRAINT repost_unique UNIQUE (user_id, submission_id);


--
-- Name: repost_relationship repost_relationship_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repost_relationship
    ADD CONSTRAINT repost_relationship_pkey PRIMARY KEY (id);


--
-- Name: comment_save_relationship comment_save_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_save_relationship
    ADD CONSTRAINT comment_save_constraint UNIQUE (user_id, comment_id);


--
-- Name: comment_save_relationship comment_save_relationship_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_save_relationship
    ADD CONSTRAINT comment_save_relationship_pkey PRIMARY KEY (id);


--
-- Name: comment_repost_relationship comment_repost_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_repost_relationship
    ADD CONSTRAINT comment_repost_unique UNIQUE (user_id, comment_id);


--
-- Name: comment_repost_relationship comment_repost_relationship_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_repost_relationship
    ADD CONSTRAINT comment_repost_relationship_pkey PRIMARY KEY (id);


--
-- Name: view_history view_history_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.view_history
    ADD CONSTRAINT view_history_constraint UNIQUE (user_id, submission_id);


--
-- Name: view_history view_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.view_history
    ADD CONSTRAINT view_history_pkey PRIMARY KEY (id);


--
-- Name: subcategories subcategories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subcategories
    ADD CONSTRAINT subcategories_pkey PRIMARY KEY (id);


--
-- Name: submissions_aux submissions_aux_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions_aux
    ADD CONSTRAINT submissions_aux_pkey PRIMARY KEY (key_id);


--
-- Name: submissions submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: titles titles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.titles
    ADD CONSTRAINT titles_pkey PRIMARY KEY (id);


--
-- Name: client_auths unique_access; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_auths
    ADD CONSTRAINT unique_access UNIQUE (access_token);


--
-- Name: client_auths unique_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_auths
    ADD CONSTRAINT unique_code UNIQUE (oauth_code);


--
-- Name: oauth_apps unique_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_apps
    ADD CONSTRAINT unique_id UNIQUE (client_id);


--
-- Name: paypal_txns unique_paypalid; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paypal_txns
    ADD CONSTRAINT unique_paypalid UNIQUE (paypal_id);


--
-- Name: client_auths unique_refresh; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_auths
    ADD CONSTRAINT unique_refresh UNIQUE (refresh_token);


--
-- Name: oauth_apps unique_secret; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oauth_apps
    ADD CONSTRAINT unique_secret UNIQUE (client_secret);


--
-- Name: badges user_badge_constraint; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges
    ADD CONSTRAINT user_badge_constraint UNIQUE (user_id, badge_id);


--
-- Name: useragents useragents_kwd_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.useragents
    ADD CONSTRAINT useragents_kwd_key UNIQUE (kwd);


--
-- Name: useragents useragents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.useragents
    ADD CONSTRAINT useragents_pkey PRIMARY KEY (id);


--
-- Name: userblocks userblocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.userblocks
    ADD CONSTRAINT userblocks_pkey PRIMARY KEY (id);


--
-- Name: userflags userflags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.userflags
    ADD CONSTRAINT userflags_pkey PRIMARY KEY (id);


--
-- Name: alts userpair; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alts
    ADD CONSTRAINT userpair UNIQUE (user1, user2);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_original_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_original_username_key UNIQUE (original_username);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (username);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: votes votes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_pkey PRIMARY KEY (id);


--
-- Name: accepted_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX accepted_idx ON public.mods USING btree (accepted);


--
-- Name: alts_user1_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alts_user1_idx ON public.alts USING btree (user1);


--
-- Name: alts_user2_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alts_user2_idx ON public.alts USING btree (user2);


--
-- Name: award_comment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX award_comment_idx ON public.award_relationships USING btree (comment_id);


--
-- Name: award_post_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX award_post_idx ON public.award_relationships USING btree (submission_id);


--
-- Name: award_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX award_user_idx ON public.award_relationships USING btree (user_id);


--
-- Name: badgedef_qual_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badgedef_qual_idx ON public.badge_defs USING btree (qualification_expr);


--
-- Name: badges_badge_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badges_badge_id_idx ON public.badges USING btree (badge_id);


--
-- Name: badges_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badges_user_index ON public.badges USING btree (user_id);


--
-- Name: badlink_link_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badlink_link_idx ON public.badlinks USING btree (link);


--
-- Name: badpic_phash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badpic_phash_idx ON public.badpics USING btree (phash);


--
-- Name: badpic_phash_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badpic_phash_trgm_idx ON public.badpics USING gin (phash public.gin_trgm_ops);


--
-- Name: badpics_phash_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX badpics_phash_index ON public.badpics USING gin (phash public.gin_trgm_ops);


--
-- Name: ban_board_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ban_board_index ON public.bans USING btree (board_id);


--
-- Name: ban_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ban_user_index ON public.bans USING btree (user_id);


--
-- Name: block_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX block_target_idx ON public.userblocks USING btree (target_id);


--
-- Name: block_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX block_user_idx ON public.userblocks USING btree (user_id);


--
-- Name: board_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX board_name_idx ON public.boards USING btree (name);


--
-- Name: board_optout_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX board_optout_idx ON public.boards USING btree (all_opt_out);


--
-- Name: board_private_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX board_private_idx ON public.boards USING btree (is_private);


--
-- Name: boardblocks_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boardblocks_idx ON public.boardblocks USING btree (user_id, board_id);


--
-- Name: boards_isbanned_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boards_isbanned_idx ON public.boards USING btree (is_banned);


--
-- Name: boards_name_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boards_name_trgm_idx ON public.boards USING gin (name public.gin_trgm_ops);


--
-- Name: boards_sensitive_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boards_sensitive_idx ON public.boards USING btree (is_sensitive);


--
-- Name: boards_sub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boards_sub_idx ON public.boards USING btree (stored_subscriber_count DESC);


--
-- Name: boards_subcat_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boards_subcat_idx ON public.boards USING btree (subcat_id);


--
-- Name: cflag_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cflag_user_idx ON public.commentflags USING btree (user_id);


--
-- Name: client_access_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX client_access_token_idx ON public.client_auths USING btree (access_token, access_token_expire_utc);


--
-- Name: client_refresh_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX client_refresh_token_idx ON public.client_auths USING btree (refresh_token);


--
-- Name: comment_banned_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_banned_idx ON public.comments USING btree (is_banned);


--
-- Name: comment_body_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_body_idx ON public.comments_aux USING btree (body) WHERE (octet_length((body)::text) <= 2704);


--
-- Name: comment_body_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_body_trgm_idx ON public.comments_aux USING gin (body public.gin_trgm_ops);


--
-- Name: comment_created_utc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_created_utc_idx ON public.comments USING btree (created_utc DESC);


--
-- Name: comment_deleted_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_deleted_idx ON public.comments USING btree (deleted_utc DESC);


--
-- Name: comment_ip_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_ip_idx ON public.comments USING btree (creation_ip);


--
-- Name: comment_post_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_post_id_index ON public.comments USING btree (parent_submission);


--
-- Name: comment_purge_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_purge_idx ON public.comments USING btree (purged_utc);


--
-- Name: commentflag_comment_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX commentflag_comment_index ON public.commentflags USING btree (comment_id);


--
-- Name: comments_aux_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comments_aux_id_idx ON public.comments_aux USING btree (id);


--
-- Name: comments_original_board_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comments_original_board_id_idx ON public.comments USING btree (original_board_id);


--
-- Name: comments_parent_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comments_parent_id_idx ON public.comments USING btree (parent_comment_id);


--
-- Name: comments_score_disputed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comments_score_disputed_idx ON public.comments USING btree (score_disputed DESC);


--
-- Name: comments_score_hot_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comments_score_hot_idx ON public.comments USING btree (score_hot DESC);


--
-- Name: comments_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comments_user_index ON public.comments USING btree (author_id);


--
-- Name: commentsaux_body_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX commentsaux_body_idx ON public.comments_aux USING gin (to_tsvector('english'::regconfig, (body)::text));


--
-- Name: commentvotes_comments_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX commentvotes_comments_id_index ON public.commentvotes USING btree (comment_id);


--
-- Name: commentvotes_comments_type_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX commentvotes_comments_type_index ON public.commentvotes USING btree (vote_type);


--
-- Name: contrib_active_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX contrib_active_index ON public.contributors USING btree (is_active);


--
-- Name: contributors_board_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX contributors_board_index ON public.contributors USING btree (board_id);


--
-- Name: contributors_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX contributors_user_index ON public.contributors USING btree (user_id);


--
-- Name: cvote_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cvote_created_idx ON public.commentvotes USING btree (created_utc);


--
-- Name: cvote_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cvote_user_index ON public.commentvotes USING btree (user_id);


--
-- Name: discord_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX discord_id_idx ON public.users USING btree (discord_id);


--
-- Name: domains_domain_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX domains_domain_trgm_idx ON public.domains USING gin (domain public.gin_trgm_ops);


--
-- Name: flag_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX flag_user_idx ON public.flags USING btree (user_id);


--
-- Name: flags_post_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX flags_post_index ON public.flags USING btree (post_id);


--
-- Name: follow_target_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX follow_target_id_index ON public.follows USING btree (target_id);


--
-- Name: follow_user_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX follow_user_id_index ON public.follows USING btree (user_id);


--
-- Name: lodge_board_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX lodge_board_idx ON public.lodges USING btree (board_id);


--
-- Name: lodge_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX lodge_name_idx ON public.lodges USING btree (name);


--
-- Name: lodge_name_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX lodge_name_trgm_idx ON public.lodges USING gin (name public.gin_trgm_ops);


--
-- Name: message_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX message_user_idx ON public.message_notifications USING btree (user_id, has_read);


--
-- Name: mod_board_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX mod_board_index ON public.mods USING btree (board_id);


--
-- Name: mod_rescind_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX mod_rescind_index ON public.mods USING btree (invite_rescinded);


--
-- Name: mod_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX mod_user_index ON public.mods USING btree (user_id);


--
-- Name: modaction_action_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX modaction_action_idx ON public.modactions USING btree (kind);


--
-- Name: modaction_board_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX modaction_board_idx ON public.modactions USING btree (board_id);


--
-- Name: modaction_cid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX modaction_cid_idx ON public.modactions USING btree (target_comment_id);


--
-- Name: modaction_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX modaction_id_idx ON public.modactions USING btree (id DESC);


--
-- Name: modaction_pid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX modaction_pid_idx ON public.modactions USING btree (target_submission_id);


--
-- Name: notification_read_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_read_idx ON public.notifications USING btree (read);


--
-- Name: notifications_comment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_comment_idx ON public.notifications USING btree (comment_id);


--
-- Name: notifications_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_user_index ON public.notifications USING btree (user_id);


--
-- Name: notifs_user_read_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifs_user_read_idx ON public.notifications USING btree (user_id, read);


--
-- Name: paypal_txn_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX paypal_txn_created_idx ON public.paypal_txns USING btree (created_utc DESC);


--
-- Name: paypal_txn_paypalid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX paypal_txn_paypalid_idx ON public.paypal_txns USING btree (paypal_id);


--
-- Name: paypal_txn_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX paypal_txn_user_id_idx ON public.paypal_txns USING btree (user_id);


--
-- Name: paypaltxn_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX paypaltxn_status_idx ON public.paypal_txns USING btree (status);


--
-- Name: post_app_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX post_app_id_idx ON public.submissions USING btree (app_id);


--
-- Name: post_public_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX post_public_idx ON public.submissions USING btree (post_public);


--
-- Name: promocode_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX promocode_active_idx ON public.promocodes USING btree (is_active);


--
-- Name: promocode_code_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX promocode_code_idx ON public.promocodes USING btree (code);


--
-- Name: reports_post_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX reports_post_index ON public.reports USING btree (post_id);


--
-- Name: sub_active_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sub_active_index ON public.subscriptions USING btree (is_active);



--
-- Name: submission_activity_disputed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_activity_disputed_idx ON public.submissions USING btree (score_disputed DESC, board_id);


--
-- Name: submission_activity_sort_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_activity_sort_idx ON public.submissions USING btree (score_activity DESC, board_id);


--
-- Name: submission_activity_top_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_activity_top_idx ON public.submissions USING btree (score_top DESC, board_id);


--
-- Name: submission_aux_url_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_aux_url_idx ON public.submissions_aux USING btree (url);


--
-- Name: submission_aux_url_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_aux_url_trgm_idx ON public.submissions_aux USING gin (url public.gin_trgm_ops);


--
-- Name: submission_best_only_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_best_only_idx ON public.submissions USING btree (score_best DESC);


--
-- Name: submission_best_sort_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_best_sort_idx ON public.submissions USING btree (score_best DESC, board_id);


--
-- Name: submission_disputed_sort_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_disputed_sort_idx ON public.submissions USING btree (is_banned, deleted_utc, score_disputed DESC);


--
-- Name: submission_hot_sort_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_hot_sort_idx ON public.submissions USING btree (is_banned, deleted_utc, score_hot DESC);


--
-- Name: submission_ip_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_ip_idx ON public.submissions USING btree (creation_ip);


--
-- Name: submission_isbanned_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_isbanned_idx ON public.submissions USING btree (is_banned);


--
-- Name: submission_isdeleted_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_isdeleted_idx ON public.submissions USING btree (deleted_utc);


--
-- Name: submission_new_sort_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_new_sort_idx ON public.submissions USING btree (is_banned, deleted_utc, created_utc DESC);


--
-- Name: submission_original_board_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_original_board_id_idx ON public.submissions USING btree (original_board_id);


--
-- Name: submission_pinned_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_pinned_idx ON public.submissions USING btree (is_pinned);


--
-- Name: submission_public_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_public_idx ON public.submissions USING btree (post_public);


--
-- Name: submission_purge_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_purge_idx ON public.submissions USING btree (purged_utc);


--
-- Name: submissions_author_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_author_index ON public.submissions USING btree (author_id);


--
-- Name: submissions_aux_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_aux_id_idx ON public.submissions_aux USING btree (id);


--
-- Name: submissions_aux_title_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_aux_title_idx ON public.submissions_aux USING btree (title);


--
-- Name: submissions_board_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_board_index ON public.submissions USING btree (board_id);


--
-- Name: submissions_created_utc_desc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_created_utc_desc_idx ON public.submissions USING btree (created_utc DESC);


--
-- Name: submissions_offensive_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_offensive_index ON public.submissions USING btree (is_offensive);



--
-- Name: submissions_sticky_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_sticky_index ON public.submissions USING btree (stickied);


--
-- Name: submissions_title_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submissions_title_trgm_idx ON public.submissions_aux USING gin (title public.gin_trgm_ops);


--
-- Name: subscription_board_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX subscription_board_index ON public.subscriptions USING btree (board_id);


--
-- Name: subscription_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX subscription_user_index ON public.subscriptions USING btree (user_id);


--
-- Name: user_banned_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_banned_idx ON public.users USING btree (is_banned);


--
-- Name: user_creation_ip_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_creation_ip_idx ON public.users USING btree (creation_ip);


--
-- Name: user_del_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_del_idx ON public.users USING btree (is_deleted);


--
-- Name: user_ip_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_ip_idx ON public.users USING btree (creation_ip);


--
-- Name: user_privacy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_privacy_idx ON public.users USING btree (is_private);


--
-- Name: userblocks_both_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX userblocks_both_idx ON public.userblocks USING btree (user_id, target_id);


--
-- Name: users_coin_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_coin_idx ON public.users USING btree (coin_balance);


--
-- Name: users_created_utc_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_created_utc_index ON public.users USING btree (created_utc);


--
-- Name: users_neg_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_neg_idx ON public.users USING btree (negative_balance_cents);


--
-- Name: users_nofollow_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_nofollow_idx ON public.users USING btree (is_nofollow);


--
-- Name: users_original_username_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_original_username_trgm_idx ON public.users USING gin (original_username public.gin_trgm_ops);


--
-- Name: users_premium_expire_utc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_premium_expire_utc_idx ON public.users USING btree (premium_expires_utc DESC);


--
-- Name: users_premium_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_premium_idx ON public.users USING btree (premium_expires_utc);


--
-- Name: users_subs_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_subs_idx ON public.users USING btree (stored_subscriber_count);


--
-- Name: users_title_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_title_idx ON public.users USING btree (title_id);


--
-- Name: users_unbanutc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_unbanutc_idx ON public.users USING btree (unban_utc DESC);


--
-- Name: users_username_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_username_trgm_idx ON public.users USING gin (username public.gin_trgm_ops);


--
-- Name: vote_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vote_created_idx ON public.votes USING btree (created_utc);


--
-- Name: vote_user_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vote_user_index ON public.votes USING btree (user_id);


--
-- Name: votes_submission_id_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX votes_submission_id_index ON public.votes USING btree (submission_id);


--
-- Name: votes_type_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX votes_type_index ON public.votes USING btree (vote_type);


--
-- Name: forwardrels_forward_submission_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX forwardrels_forward_submission_id_idx ON public.forwardrels USING btree (forward_submission_id);


--
-- Name: forwardrels_forwarded_by_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX forwardrels_forwarded_by_id_idx ON public.forwardrels USING btree (forwarded_by_id);


--
-- Name: comment_forwardrels_promoted_submission_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_forwardrels_promoted_submission_id_idx ON public.comment_forwardrels USING btree (promoted_submission_id);


--
-- Name: comment_forwardrels_promoted_by_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX comment_forwardrels_promoted_by_id_idx ON public.comment_forwardrels USING btree (promoted_by_id);


--
-- Name: badges badges_badge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.badges
    ADD CONSTRAINT badges_badge_id_fkey FOREIGN KEY (badge_id) REFERENCES public.badge_defs(id);


--
-- Name: bans bans_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bans
    ADD CONSTRAINT bans_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: contributors board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contributors
    ADD CONSTRAINT board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: commentflags commentflags_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commentflags
    ADD CONSTRAINT commentflags_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.comments(id);


--
-- Name: flags flags_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flags
    ADD CONSTRAINT flags_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.submissions(id);


--
-- Name: mods mods_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mods
    ADD CONSTRAINT mods_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: notifications notifications_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.comments(id);


--
-- Name: postrels postrels_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postrels
    ADD CONSTRAINT postrels_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: postrels postrels_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.postrels
    ADD CONSTRAINT postrels_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.submissions(id);


--
-- Name: forwardrels forwardrels_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forwardrels
    ADD CONSTRAINT forwardrels_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: forwardrels forwardrels_primary_submission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forwardrels
    ADD CONSTRAINT forwardrels_primary_submission_id_fkey FOREIGN KEY (primary_submission_id) REFERENCES public.submissions(id);


--
-- Name: forwardrels forwardrels_forward_submission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forwardrels
    ADD CONSTRAINT forwardrels_forward_submission_id_fkey FOREIGN KEY (forward_submission_id) REFERENCES public.submissions(id);


--
-- Name: comment_forwardrels comment_forwardrels_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_forwardrels
    ADD CONSTRAINT comment_forwardrels_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.comments(id);


--
-- Name: comment_forwardrels comment_forwardrels_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_forwardrels
    ADD CONSTRAINT comment_forwardrels_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: comment_forwardrels comment_forwardrels_promoted_submission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_forwardrels
    ADD CONSTRAINT comment_forwardrels_promoted_submission_id_fkey FOREIGN KEY (promoted_submission_id) REFERENCES public.submissions(id);


--
-- Name: repost_relationship repost_relationship_submission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.repost_relationship
    ADD CONSTRAINT repost_relationship_submission_id_fkey FOREIGN KEY (submission_id) REFERENCES public.submissions(id);


--
-- Name: comment_save_relationship comment_save_relationship_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_save_relationship
    ADD CONSTRAINT comment_save_relationship_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.comments(id);


--
-- Name: comment_repost_relationship comment_repost_relationship_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_repost_relationship
    ADD CONSTRAINT comment_repost_relationship_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.comments(id);




--
-- Name: reports reports_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.submissions(id);


--
-- Name: subcategories subcategories_cat_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subcategories
    ADD CONSTRAINT subcategories_cat_id_fkey FOREIGN KEY (cat_id) REFERENCES public.categories(id);


--
-- Name: submissions submissions_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: submissions submissions_original_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submissions
    ADD CONSTRAINT submissions_original_board_id_fkey FOREIGN KEY (original_board_id) REFERENCES public.boards(id);


--
-- Name: subscriptions subscriptions_board_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);


--
-- Name: users users_title_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_title_fkey FOREIGN KEY (title_id) REFERENCES public.titles(id);


--
-- Ruqqus "About this Account" / Region feature additions
--

--
-- Name: login_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_events (
    id integer NOT NULL,
    user_id integer,
    created_utc integer DEFAULT 0,
    ip character varying(255),
    cf_country character(2),
    region_code character varying(32)
);

CREATE SEQUENCE public.login_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.login_events_id_seq OWNED BY public.login_events.id;

ALTER TABLE ONLY public.login_events ALTER COLUMN id SET DEFAULT nextval('public.login_events_id_seq'::regclass);

ALTER TABLE ONLY public.login_events
    ADD CONSTRAINT login_events_pkey PRIMARY KEY (id);

CREATE INDEX login_event_user_created_idx ON public.login_events USING btree (user_id, created_utc DESC);

--
-- Name: regions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regions (
    id integer NOT NULL,
    code character varying(32) NOT NULL,
    default_name character varying(64),
    current_name character varying(64),
    color character(7)
);

CREATE SEQUENCE public.regions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.regions_id_seq OWNED BY public.regions.id;

ALTER TABLE ONLY public.regions ALTER COLUMN id SET DEFAULT nextval('public.regions_id_seq'::regclass);

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_code_key UNIQUE (code);

--
-- Name: region_name_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.region_name_proposals (
    id integer NOT NULL,
    region_id integer,
    proposed_name character varying(64),
    created_by_id integer,
    created_utc integer DEFAULT 0,
    vote_count integer DEFAULT 0
);

CREATE SEQUENCE public.region_name_proposals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.region_name_proposals_id_seq OWNED BY public.region_name_proposals.id;

ALTER TABLE ONLY public.region_name_proposals ALTER COLUMN id SET DEFAULT nextval('public.region_name_proposals_id_seq'::regclass);

ALTER TABLE ONLY public.region_name_proposals
    ADD CONSTRAINT region_name_proposals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.region_name_proposals
    ADD CONSTRAINT region_name_proposals_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id);

CREATE INDEX region_name_proposals_region_id_idx ON public.region_name_proposals USING btree (region_id);

--
-- Name: region_name_votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.region_name_votes (
    id integer NOT NULL,
    region_id integer,
    proposal_id integer,
    user_id integer,
    created_utc integer DEFAULT 0
);

CREATE SEQUENCE public.region_name_votes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.region_name_votes_id_seq OWNED BY public.region_name_votes.id;

ALTER TABLE ONLY public.region_name_votes ALTER COLUMN id SET DEFAULT nextval('public.region_name_votes_id_seq'::regclass);

ALTER TABLE ONLY public.region_name_votes
    ADD CONSTRAINT region_name_votes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.region_name_votes
    ADD CONSTRAINT uq_region_name_votes_region_user UNIQUE (region_id, user_id);

ALTER TABLE ONLY public.region_name_votes
    ADD CONSTRAINT region_name_votes_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES public.region_name_proposals(id);

ALTER TABLE ONLY public.region_name_votes
    ADD CONSTRAINT region_name_votes_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id);


--
-- Ruqqus "Curations" feature additions
--

--
-- Name: curations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curations (
    id bigint NOT NULL,
    owner_id bigint,
    name character varying(100),
    slug character varying(25),
    description character varying(500) DEFAULT ''::character varying,
    is_private boolean DEFAULT true,
    created_utc bigint DEFAULT 0,
    forked_from_id bigint,
    region_filter character varying(500) DEFAULT ''::character varying,
    language_filter character varying(500) DEFAULT ''::character varying,
    category_filter character varying(500) DEFAULT ''::character varying
);

CREATE SEQUENCE public.curations_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.curations_id_seq OWNED BY public.curations.id;

ALTER TABLE ONLY public.curations ALTER COLUMN id SET DEFAULT nextval('public.curations_id_seq'::regclass);

ALTER TABLE ONLY public.curations
    ADD CONSTRAINT curations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.curations
    ADD CONSTRAINT curations_slug_key UNIQUE (slug);

-- Note: no FK on owner_id -> users(id); users' primary key is on username,
-- not id (pre-existing schema quirk), so id has no unique constraint for a
-- real FK to target - matches the existing sparse-FK convention already used
-- throughout this schema (e.g. paypal_txns.user_id has no FK either).
ALTER TABLE ONLY public.curations
    ADD CONSTRAINT curations_forked_from_id_fkey FOREIGN KEY (forked_from_id) REFERENCES public.curations(id);

CREATE INDEX curations_owner_id_idx ON public.curations USING btree (owner_id);

--
-- Name: curation_guilds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curation_guilds (
    id bigint NOT NULL,
    curation_id bigint,
    board_id bigint,
    created_utc bigint DEFAULT 0
);

CREATE SEQUENCE public.curation_guilds_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.curation_guilds_id_seq OWNED BY public.curation_guilds.id;

ALTER TABLE ONLY public.curation_guilds ALTER COLUMN id SET DEFAULT nextval('public.curation_guilds_id_seq'::regclass);

ALTER TABLE ONLY public.curation_guilds
    ADD CONSTRAINT curation_guilds_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.curation_guilds
    ADD CONSTRAINT uq_curation_guilds_curation_board UNIQUE (curation_id, board_id);

ALTER TABLE ONLY public.curation_guilds
    ADD CONSTRAINT curation_guilds_curation_id_fkey FOREIGN KEY (curation_id) REFERENCES public.curations(id);

ALTER TABLE ONLY public.curation_guilds
    ADD CONSTRAINT curation_guilds_board_id_fkey FOREIGN KEY (board_id) REFERENCES public.boards(id);

CREATE INDEX curation_guilds_curation_id_idx ON public.curation_guilds USING btree (curation_id);

--
-- Name: curation_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curation_users (
    id bigint NOT NULL,
    curation_id bigint,
    target_user_id bigint,
    created_utc bigint DEFAULT 0
);

CREATE SEQUENCE public.curation_users_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.curation_users_id_seq OWNED BY public.curation_users.id;

ALTER TABLE ONLY public.curation_users ALTER COLUMN id SET DEFAULT nextval('public.curation_users_id_seq'::regclass);

ALTER TABLE ONLY public.curation_users
    ADD CONSTRAINT curation_users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.curation_users
    ADD CONSTRAINT uq_curation_users_curation_user UNIQUE (curation_id, target_user_id);

ALTER TABLE ONLY public.curation_users
    ADD CONSTRAINT curation_users_curation_id_fkey FOREIGN KEY (curation_id) REFERENCES public.curations(id);

-- no FK on target_user_id -> users(id), same reason as curations.owner_id above

CREATE INDEX curation_users_curation_id_idx ON public.curation_users USING btree (curation_id);
CREATE INDEX curation_users_target_user_id_idx ON public.curation_users USING btree (target_user_id);

--
-- Name: curation_follows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curation_follows (
    id bigint NOT NULL,
    curation_id bigint,
    user_id bigint,
    created_utc bigint DEFAULT 0
);

CREATE SEQUENCE public.curation_follows_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.curation_follows_id_seq OWNED BY public.curation_follows.id;

ALTER TABLE ONLY public.curation_follows ALTER COLUMN id SET DEFAULT nextval('public.curation_follows_id_seq'::regclass);

ALTER TABLE ONLY public.curation_follows
    ADD CONSTRAINT curation_follows_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.curation_follows
    ADD CONSTRAINT uq_curation_follows_curation_user UNIQUE (curation_id, user_id);

ALTER TABLE ONLY public.curation_follows
    ADD CONSTRAINT curation_follows_curation_id_fkey FOREIGN KEY (curation_id) REFERENCES public.curations(id);

-- no FK on user_id -> users(id), same reason as curations.owner_id above

CREATE INDEX curation_follows_curation_id_idx ON public.curation_follows USING btree (curation_id);
CREATE INDEX curation_follows_user_id_idx ON public.curation_follows USING btree (user_id);


--
-- Ruqqus content removal/edit audit trail additions
--

--
-- Name: content_edit_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_edit_history (
    id bigint NOT NULL,
    actor_id integer,
    target_submission_id integer DEFAULT 0,
    target_comment_id integer DEFAULT 0,
    board_id integer DEFAULT 0,
    action character varying(32),
    reason character varying(256),
    previous_title character varying(500),
    previous_url character varying(500),
    previous_body character varying(25000),
    previous_body_html character varying(50000),
    created_utc integer DEFAULT 0
);

CREATE SEQUENCE public.content_edit_history_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.content_edit_history_id_seq OWNED BY public.content_edit_history.id;

ALTER TABLE ONLY public.content_edit_history ALTER COLUMN id SET DEFAULT nextval('public.content_edit_history_id_seq'::regclass);

ALTER TABLE ONLY public.content_edit_history
    ADD CONSTRAINT content_edit_history_pkey PRIMARY KEY (id);

CREATE INDEX content_edit_history_sid_idx ON public.content_edit_history USING btree (target_submission_id);
CREATE INDEX content_edit_history_cid_idx ON public.content_edit_history USING btree (target_comment_id);
CREATE INDEX content_edit_history_board_idx ON public.content_edit_history USING btree (board_id);

--
-- Name: obliteration_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.obliteration_records (
    id bigint NOT NULL,
    actor_id integer,
    author_id integer,
    target_submission_id integer DEFAULT 0,
    target_comment_id integer DEFAULT 0,
    board_id integer DEFAULT 0,
    reason_category character varying(32),
    reason character varying(512),
    created_utc integer DEFAULT 0
);

CREATE SEQUENCE public.obliteration_records_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.obliteration_records_id_seq OWNED BY public.obliteration_records.id;

ALTER TABLE ONLY public.obliteration_records ALTER COLUMN id SET DEFAULT nextval('public.obliteration_records_id_seq'::regclass);

ALTER TABLE ONLY public.obliteration_records
    ADD CONSTRAINT obliteration_records_pkey PRIMARY KEY (id);

CREATE INDEX obliteration_records_sid_idx ON public.obliteration_records USING btree (target_submission_id);
CREATE INDEX obliteration_records_cid_idx ON public.obliteration_records USING btree (target_comment_id);

--
-- Name: chat_identities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_identities (
    user_id integer NOT NULL,
    matrix_user_id character varying(255) NOT NULL,
    provisioned_utc integer DEFAULT 0,
    recovery_key_encrypted text
);

ALTER TABLE ONLY public.chat_identities
    ADD CONSTRAINT chat_identities_pkey PRIMARY KEY (user_id);

CREATE UNIQUE INDEX chat_identities_matrix_user_id_idx ON public.chat_identities USING btree (matrix_user_id);

--
-- Name: chat_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_conversations (
    id bigint NOT NULL,
    user_a_id integer NOT NULL,
    user_b_id integer NOT NULL,
    initiator_id integer NOT NULL,
    matrix_room_id character varying(255) NOT NULL,
    status character varying(16) DEFAULT 'request'::character varying NOT NULL,
    created_utc integer DEFAULT 0,
    last_activity_utc integer DEFAULT 0,
    CONSTRAINT chat_conversations_pair_order CHECK (user_a_id < user_b_id)
);

CREATE SEQUENCE public.chat_conversations_id_seq
    AS bigint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.chat_conversations_id_seq OWNED BY public.chat_conversations.id;

ALTER TABLE ONLY public.chat_conversations ALTER COLUMN id SET DEFAULT nextval('public.chat_conversations_id_seq'::regclass);

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT chat_conversations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT uq_chat_conversations_pair UNIQUE (user_a_id, user_b_id);

CREATE UNIQUE INDEX chat_conversations_room_id_idx ON public.chat_conversations USING btree (matrix_room_id);
CREATE INDEX chat_conversations_user_a_idx ON public.chat_conversations USING btree (user_a_id);
CREATE INDEX chat_conversations_user_b_idx ON public.chat_conversations USING btree (user_b_id);
CREATE INDEX chat_conversations_status_idx ON public.chat_conversations USING btree (status);

--
-- Name: chat_unread; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_unread (
    conversation_id bigint NOT NULL,
    user_id integer NOT NULL,
    unread_count integer DEFAULT 0,
    last_read_utc integer DEFAULT 0
);

ALTER TABLE ONLY public.chat_unread
    ADD CONSTRAINT chat_unread_pkey PRIMARY KEY (conversation_id, user_id);

ALTER TABLE ONLY public.chat_unread
    ADD CONSTRAINT chat_unread_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chat_conversations(id);

CREATE INDEX chat_unread_user_id_idx ON public.chat_unread USING btree (user_id);


--
-- PostgreSQL database dump complete
--

--
-- Word filter list (see ruqqus/classes/word_filter.py). An empty table means
-- "use the built-in starter list" (ruqqus/helpers/wordfilter_seed.py).
--

CREATE TABLE public.word_filter_entries (
    id SERIAL PRIMARY KEY,
    word character varying(64) NOT NULL,
    severity smallint DEFAULT 1 NOT NULL,
    mode character varying(16) DEFAULT 'word'::character varying NOT NULL,
    variants character varying(512) DEFAULT ''::character varying,
    suffixes character varying(512) DEFAULT NULL,
    enabled boolean DEFAULT true NOT NULL,
    note character varying(256) DEFAULT ''::character varying,
    created_utc integer DEFAULT 0,
    CONSTRAINT word_filter_entries_word_key UNIQUE (word)
);

CREATE TABLE public.post_templates (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    name character varying(60) NOT NULL,
    body text NOT NULL,
    created_utc integer DEFAULT 0
);

CREATE INDEX post_templates_user_id_index ON public.post_templates USING btree (user_id);

CREATE INDEX submissions_word_severity_index ON public.submissions USING btree (word_severity);
CREATE INDEX comments_word_severity_index ON public.comments USING btree (word_severity);
