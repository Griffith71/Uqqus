-- A post linking to a named video platform whose oEmbed call failed at submit time was saved with the
-- page's own og:video player as "iframe_untrusted" (strict sandbox), where the platform's player cannot
-- load, so it never played (an Odysee post of 2026-10-04). New posts get "iframe" through
-- helpers/embed_trust.same_provider_player; this repairs the ones already saved. Data only, safe to run twice.
-- The domains are the "iframe" entries of KNOWN_PROVIDER_HANDLERS in ruqqus/helpers/embed.py.

UPDATE submissions_aux a
SET embed_type = 'iframe'
FROM (VALUES
    ('youtube.com'), ('youtu.be'), ('vimeo.com'), ('dailymotion.com'), ('odysee.com'),
    ('bitchute.com'), ('rumble.com'), ('twitch.tv'), ('bilibili.com')
) AS provider(domain)
WHERE a.embed_type = 'iframe_untrusted'
  AND lower(substring(a.embed_url FROM '^https://([^/:?#]+)')) ~ ('(^|\.)' || replace(provider.domain, '.', '\.') || '$')
  AND lower(substring(a.url FROM '^https?://([^/:?#]+)')) ~ ('(^|\.)' || replace(provider.domain, '.', '\.') || '$');
