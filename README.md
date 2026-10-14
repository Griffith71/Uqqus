<p align="center">
<img src="https://raw.githubusercontent.com/ruqqus/ruqqus/master/ruqqus/assets/images/logo/ruqqus_text_logo.png" width="250"/>
</p>

<hr>

# Ruqqus

Ruqqus is an open-source platform for independent internet communities.

[![Build status](https://travis-ci.com/ruqqus/ruqqus.svg?branch=master)](https://travis-ci.com/ruqqus) ![Snyk Vulnerabilities for GitHub Repo](https://img.shields.io/snyk/vulnerabilities/github/ruqqus/ruqqus) [![Website](https://img.shields.io/website/https/www.ruqqus.com?down_color=red&down_message=down&up_message=up)](https://www.ruqqus.com) ![GitHub language count](https://img.shields.io/github/languages/count/ruqqus/ruqqus) ![GitHub code size in bytes](https://img.shields.io/github/languages/code-size/ruqqus/ruqqus) ![Lines of code](https://img.shields.io/tokei/lines/github/ruqqus/ruqqus) ![GitHub](https://img.shields.io/github/license/ruqqus/ruqqus) [![Discord](https://img.shields.io/discord/599258778520518676)](https://ruqqus.com/discord)

<p align="center">
<img src="https://raw.githubusercontent.com/ruqqus/ruqqus/master/ruqqus/assets/images/preview-images/ruqqus_demo.png" width="720"/>
</p>

## Features

- Moderator power limited by design
- No ads
- US-based servers
- Mobile friendly
- Dark mode

## Why Ruqqus?

Every post is first posted on its author's own profile. Authors (and anyone else) can then forward a post or comment into a community (guild) for discussion there. A moderator can hide forwarded content from their guild, but never delete it off the platform entirely - it stays on the author's profile. Content that violates the [site-wide policy](https://ruqqus.com/help/terms) is removed by the core team.

Moderators, called guild masters, can lead at most 5 guilds, and the accounts on one network at most 5 between them.

We do not serve ads. Put simply, advertisements lead to censorship. Ruqqus is funded out-of-pocket by the core team and through donations from users.

Ruqqus is responsive and mobile browser-friendly.

## Getting started

An account is not required to browse Ruqqus but we recommend creating one.

**1. Create an account**

[Sign up](https://ruqqus.com/signup?ref=ruqqus) in seconds, no email required. With a Ruqqus account, you can vote and comment on posts as well as join guilds.

**2. Join some guilds**

After signing up, we recommend you join some guilds. Your home feed will be populated by content from guilds you've joined.

**3. Create a post**

On Ruqqus, you can share links or text posts.

## Contributing

Pull requests are welcome! For major changes, please open an issue to discuss what you would like to change.

## Sponsors

As an open-source project, we are supported by the community. If you would like to support the development of Ruqqus, please consider [making a donation](https://ruqqus.com/help/donate) :)

**BTC** - `16JFRF4sXQ9BvY3w73MD64yPUKehhUtste`

**LTC** - `LNDKsNhHjiNBJ6YBWtE8io8H1W3Fv5mtEd`

**ETH**/**ERC20** - `0x4301c31B81C2C66f5aaDFC1ec75861ad3d3cE0cC`

**XMR** - `8BkNezPQex6eTanNQHswMmhCAqWkXzVNEDnvnBN1YSxmic87E2VZncNVDiS1TCjuUs4K34FiGVBK82AfkT2NYsxFN6cYTpy`

**ZEC** - `t1VqHznyeQnS9hNaU32ksijfT9s9DTXgUSv`

**BCH** - `qpcg2czl2fa8x43z807ta5kpuzd3g4suecuu9lmqug`

**XRP** - `rBWWxvREbRPSqMGNqwpBVbSJMPkmLum5xR`

**XLM** - `GBB4FE2UYMUPHCYYLZFT3WSAOSEKI7YIBSOLNEPU6GPWAU64OXOQOA3Q`

**DASH** - `Xj5dmVJXVjCxuyKRhTmSJ6p1i3ehWLV9gT`

**DOGE** - `DT9j54NhxcH3VTU1QeuWcifyaLayTPuv1U`

**VTC** - `3Lq9vNtphMM3rtuzLhtNUjn5WhSk7ZMQYK`

**PIVX** - `DSgD6hxu1EgRiULn6RNbnv5tRvij5BGtUa`

## Stay in touch

- [Twitter](https://twitter.com/ruqqus)
- [Discord](https://ruqqus.com/discord)
- [Twitch.tv](https://twitch.tv/captainmeta4)

## Local development

### Docker Deployment

The legacy app runs with Docker Compose (Postgres, Redis, Synapse and the app itself):

```
python3 scripts/gen_dev_env.py     # writes MASTER_KEY and the Matrix tokens to .env (never committed)
docker compose up --build
```

The site is then at http://localhost. The stylesheets are compiled from `ruqqus/assets/style/*.scss` by the
server, so there is nothing to build first.

A fresh database is created from `schema.sql` and `seed-db.sql`: the categories, the regions and the baseline
guilds (including `systemprofile`, where every post is first made). No account is seeded: sign up, then make
yourself an admin with `docker compose exec postgres psql -U postgres -c "UPDATE users SET admin_level=5 WHERE username='you'"`.
An existing database needs the files in `scripts/migrations/` applied in date order (each is safe to run twice).

### Mac

Install dependencies

`$ pip3 install -r requirements.txt`

`$ brew install redis`

`$ brew install postgres`





## Stay in touch

- [Twitter](https://twitter.com/ruqqus)
- [Discord](https://ruqqus.com/discord)
- [Twitch.tv](https://twitch.tv/captainmeta4)

## Local development

### [Mac](https://github.com/ruqqus/ruqqus/wiki/Mac-Installation)

### [Linux](https://github.com/ruqqus/ruqqus/wiki/Linux-Installation)

The word filter works out of the box: with an empty `word_filter_entries` table it uses the built-in list
(`ruqqus/helpers/wordfilter_seed.py`), editable at `/admin/word_filter`. Run every check with `bash scripts/check.sh`
(see `CLAUDE.md`).

## API

Community-made docs for the Ruqqus API can be found here:
https://ruqqus-api.stoplight.io

## License
[MPL-2.0](https://github.com/ruqqus/ruqqus/blob/master/LICENSE)

