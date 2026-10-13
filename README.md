# ROYAL X CHECKER

**Payment Security & Testing Lab** — an asynchronous Telegram bot for synthetic payment simulations, documented provider test flows, and authorized education.

> **Safety boundary:** This project does not accept card numbers or CVVs, generate usable payment credentials, perform live payment authorizations, check whether a card is active, or bypass payment-provider controls. Local simulation results are always labelled synthetic. Stripe test mode is disabled until explicitly configured with a Stripe `sk_test_` key.

The repository began as a README-only stub. The supplied reference PDF/screenshots were not present in the checkout, so the interface follows the menu hierarchy described in the product brief and uses original Royal X branding rather than copying unavailable artwork or undocumented behavior.

## Features

- Premium, mobile-friendly Telegram inline navigation: **Gates**, **Tools**, **Plans**, **Profile**, **Feedback**, **Help**, and an owner-only admin panel.
- **AUTH:** an implemented Stripe PaymentIntent adapter restricted to Stripe test mode and the documented `pm_card_visa` test PaymentMethod. The bot retrieves the PaymentIntent server-side before displaying its status. No card details are requested or stored.
- **CHARGE:** a local-only transaction simulator with routine, unusual-amount, repeated-attempt, rapid-location-change, and random scenarios. Bounded automatic batches produce synthetic approval, decline, suspicious, and simulated-error categories plus a CSV report.
- Synthetic fictional profiles and transaction records. Generated emails use the reserved `.invalid` domain.
- BIN/IIN lookup through the documented public Binlist metadata endpoint. Only 6- or 8-digit prefixes are sent; this is metadata lookup, not an authorization or active-card check.
- CSV/text file analysis for explicitly labelled synthetic records, with strict size/row/column validation, apparent payment-credential rejection, duplicate detection, heuristic risk summaries, safe CSV export, and short-lived export files.
- Transparent, configurable heuristic fraud analytics; no claim that rule-based output is a definitive fraud determination.
- Persisted user profiles, UTC daily quotas, transactional credit ledger/deductions, idempotency, manual plan activation requests, feedback review, admin actions, and audit records.
- SQLite for the initial setup; PostgreSQL via `asyncpg` is supported. Alembic migrations run automatically at startup.
- JSON structured logs to stdout. Secrets, raw PANs, and CVVs are not logged or persisted.

Telegram clients control their own dark/light appearance. The bot uses consistent black-and-gold-inspired text, emoji accents, HTML formatting, short status messages, and native inline keyboards; it does not pretend Telegram supports arbitrary native app animations.

## Implemented menus

### Main menu

- **Gates:** AUTH, local simulator, Fraud Lab, and test history.
- **Tools:** enabled BIN lookup, synthetic data, file analyzer, and fraud analytics.
- **Plans:** Free, Basic, Premium and manual activation request status.
- **Profile:** persisted Telegram ID, plan, credits, UTC registration date, and today's usage.
- **Feedback:** persisted submissions with an authorized admin review queue.
- **Help:** commands, limitations, and setup details.
- **Owner Panel:** shown only to configured owners; every callback and administrative input is also checked server-side.

### Plans and credits

Initial database seed values are editable defaults, not a payment promise:

| Plan | Default duration | Default price | UTC daily operation quota |
|---|---:|---:|---:|
| Free | No expiry | USD 0.00 | 10 |
| Basic | 30 days | USD 4.99 | 100 |
| Premium | 30 days | USD 14.99 | 500 |

The bot has **manual activation only**. An admin must verify any payment independently before activating a plan. A screenshot or user claim never activates a subscription. There is no automated subscription-payment processor in this version.

New users receive a configurable development credit balance (default 5). Default operation costs are 2 credits for an external Stripe sandbox test, 1 for BIN lookup, and 1 for file analysis; local simulations, synthetic data, and fraud analysis are free but count against a daily plan quota. Owners can configure costs, plan prices, quotas, and feature switches from the admin panel. Failed validation/provider operations are refunded; uncertain Stripe responses hold credits until an idempotent retry confirms the request.

## Requirements

- Python **3.12+** for normal installation/deployment.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- At least one trusted numeric Telegram owner ID in `OWNER_IDS`.
- SQLite for development, or PostgreSQL for a hosted deployment.
- Optional Stripe **test-mode** secret key. No Stripe credentials are required to use local features.

This sandbox checkout provided Python 3.11.2, so automated tests were run against Python 3.11 for compatibility; production metadata and the Docker image target Python 3.12+. A Python 3.12 runtime check remains for deployment.

## Configure and run locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Edit `.env` locally. **Never commit it.** Required values:

```dotenv
BOT_TOKEN=your-token-from-BotFather
OWNER_IDS=123456789
DATABASE_URL=sqlite+aiosqlite:///./data/royal_x_checker.db
STRIPE_SANDBOX_ENABLED=false
STRIPE_SECRET_KEY=
```

Then start the polling bot:

```bash
python -m app.main
```

Database migrations and default plan seeding run during startup. The bot stops with a safe configuration error if `BOT_TOKEN` or `OWNER_IDS` is missing. A present Stripe secret is rejected unless it starts with `sk_test_`; sandbox requests also remain blocked unless `STRIPE_SANDBOX_ENABLED=true`.

Run tests:

```bash
python -m pytest -q
```

Useful checks:

```bash
python -m compileall -q app migrations scripts tests
python -m ruff check app migrations scripts tests
python -m ruff format --check app migrations scripts tests
alembic current
```

## Getting the owner ID from a phone

1. Create the bot in BotFather and set `BOT_TOKEN` in a secure terminal environment (or a local untracked `.env`).
2. Before the polling worker is running, send `/whoami` to your new bot.
3. In the repository terminal, run `python scripts/read_owner_id.py`. It calls Telegram's `getUpdates` API using the token from environment configuration and prints only numeric IDs for pending `/whoami` messages; it does not print the token or message contents.
4. Add your own ID to the host's `OWNER_IDS` setting, comma-separated if there are multiple owners. The polling bot will later answer `/whoami` directly as well.

Only use a trusted environment for bot tokens. Do not paste tokens into source files, GitHub issues, chat, or shell commands that put the token in command history.

## Android-first workflow

A desktop is not required:

1. Open the repository in Chrome on Android and sign in to GitHub.
2. Use GitHub's web editor for source/documentation changes, or open **Code → Codespaces → Create codespace** for a cloud terminal. In Codespaces, open a terminal and follow the local setup commands above with Python 3.12.
3. Use Codespaces secrets or the hosting provider's environment-variable dashboard for `BOT_TOKEN`, `OWNER_IDS`, database URL, and optional Stripe test key. Do not create or commit a real `.env` file in the GitHub editor.
4. Use the cloud terminal for short development tests. A codespace can stop or sleep; it is not suitable as the bot's permanent host.
5. Deploy a long-running **background worker** from the repository for continuous polling, and inspect its log dashboard from the phone browser.

The GitHub web editor changes code; it does not keep a Telegram bot process running.

## Deploy with Docker on Render (background worker)

Polling is the supported deployment method in this release. Use one continuously running worker; do not run several copies of the same polling bot. No inbound web port is required.

1. Push/deploy the repository branch containing this implementation to GitHub.
2. In Render, create a **Background Worker** and connect the repository. Select the Docker runtime so Render uses the included `Dockerfile` and its `python:3.12-slim` image. The container command is already `python -m app.main`.
3. Create a Render PostgreSQL database for a durable hosted deployment. Set `DATABASE_URL` to its internal URL. The app accepts `postgresql://` and `postgres://` URLs and normalizes them to the async PostgreSQL driver; `postgresql+asyncpg://` is also accepted.
4. Add these environment variables in Render's service dashboard:

   - `BOT_TOKEN` — BotFather token (secret).
   - `OWNER_IDS` — trusted numeric Telegram IDs.
   - `DATABASE_URL` — internal PostgreSQL URL.
   - `STRIPE_SANDBOX_ENABLED=false` — keep Stripe off until intentionally configured.
   - `BIN_LOOKUP_ENABLED=true` — public metadata lookup only; set false to disable it.
   - `LOG_LEVEL=INFO`.

5. Save/redeploy. Check the worker logs for `Royal X Checker polling bot initialized`; migrations run at startup. Message the bot with `/start`.
6. To enable Stripe sandbox later, set `STRIPE_SANDBOX_ENABLED=true` and `STRIPE_SECRET_KEY` to a Stripe **test-mode** key from the provider dashboard's test environment, then redeploy. Never use a live key. The UI displays setup/disabled state if credentials are missing.
7. Use Render's log view for errors/restarts. A failed startup commonly means a missing token/owner ID, invalid database URL, or database connectivity problem.

A single polling worker is deliberate: Telegram polling cannot safely be shared by multiple replicas. For a multi-instance webhook deployment, implement a separately reviewed webhook service with HTTPS, signature validation where applicable, and shared coordination; webhooks are not part of this codebase.

## Configuration reference

| Setting | Default | Purpose |
|---|---|---|
| `BOT_TOKEN` | unset | Required Telegram bot token; secret. |
| `OWNER_IDS` | unset | Required comma-separated numeric owner IDs. |
| `DATABASE_URL` | local SQLite | `sqlite+aiosqlite:///...` or PostgreSQL URL. |
| `STRIPE_SANDBOX_ENABLED` | `false` | Explicit opt-in for test-mode Stripe only. |
| `STRIPE_SECRET_KEY` | unset | Optional `sk_test_` key; never a live key. |
| `STRIPE_TIMEOUT_SECONDS` | `12` | Bounded provider timeout. |
| `BIN_LOOKUP_ENABLED` | `true` | Public Binlist metadata adapter switch. |
| `BIN_LOOKUP_TIMEOUT_SECONDS` | `5` | Bounded public metadata request timeout. |
| `BIN_LOOKUP_CACHE_SECONDS` | `86400` | In-process cache lifetime. |
| `MAX_FILE_SIZE_BYTES` | `1048576` | CSV/text upload cap (1 MiB). |
| `MAX_FILE_ROWS` | `1000` | Imported synthetic record cap. |
| `MAX_TEST_COUNT` | `20` | Synthetic/batch generation cap. |
| `MAX_SIMULATION_AMOUNT_MINOR` | `1000000` | Local simulation cap in minor currency units. |
| `MAX_FEEDBACK_CHARS` | `2000` | Feedback input cap. |
| `FREE_INITIAL_CREDITS` | `5` | Development credits granted once to a new user. |
| `RATE_LIMIT_COUNT` | `30` | In-process user requests per sliding window. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding rate-limit window. |
| `SIMULATION_RULES` | JSON defaults | Per-scenario probabilities; each scenario must total 1.0. |
| `FEATURE_COSTS` | JSON defaults | Whole-number credit costs; owner can override in the database. |

The in-memory rate limiter and BIN cache are suitable for the single-worker polling deployment. Multiple replicas would need a shared rate-limit/cache store.

## External integrations

### Stripe test mode

- Official documentation: [Testing](https://docs.stripe.com/testing), [Create a PaymentIntent](https://docs.stripe.com/api/payment_intents/create), and [Retrieve a PaymentIntent](https://docs.stripe.com/api/payment_intents/retrieve).
- Uses Stripe's documented `pm_card_visa` test PaymentMethod; the bot never accepts a PAN, CVV, or arbitrary payment method ID from a Telegram user.
- The PaymentIntent is created in test mode with an idempotency key, then retrieved server-side before the normalized result is recorded.
- Provider API errors are not reported as approvals. Ambiguous network outcomes keep the same idempotency key for retry.
- No Stripe webhooks are used by this polling implementation, so there is no webhook endpoint to configure. If webhooks are added later, signature verification and duplicate-event handling are mandatory.
- The live integration was **not configured or exercised** in this checkout because no authorized Stripe test credentials were supplied. Unit tests use mocked HTTP responses only.

### BIN/IIN metadata

- Provider: [Binlist public API](https://binlist.net/).
- Sends only a validated six- or eight-digit prefix to `https://lookup.binlist.net/`.
- Uses a configured timeout, positive-result cache, and per-user rate limit. Missing fields are displayed as unavailable; provider/network failures are safe and do not invent values.
- No provider key is bundled. Disable with `BIN_LOOKUP_ENABLED=false` if the deployment should not make public metadata requests.

## File import contract

Uploads must be UTF-8 `.csv` or `.txt` with a header and the `synthetic_label` and `amount` columns. Every row must identify itself as `synthetic` or `test`. Supported columns are:

`synthetic_label, transaction_id, customer_id, merchant_id, amount, currency, timestamp, country, location, attempt_count, status`

Unknown columns—including names/emails, raw card fields, CVV/CVC, expiry, or track data—are rejected. Apparent 12–19 digit payment-number sequences and explicit security-code patterns are rejected even if a Luhn check would fail. Input bytes are held in memory for analysis, not stored in the database or written to an upload directory. Export files live in a temporary directory and are deleted after Telegram finishes uploading them. Files are never executed.

## Database and backups

Models include users, plans, subscriptions, credit ledger, usage, sandbox transactions, simulation results, feedback, audit logs, and application settings. Foreign keys, uniqueness, checks, and indexes protect balances and idempotency. Timestamps are stored as UTC. Raw card numbers and CVVs have no model fields.

Migrations are in `migrations/versions/` and run automatically during bot startup. To inspect the current migration in a configured environment:

```bash
alembic current
alembic history
```

SQLite development backup (stop the worker first, then copy the database file to secure storage):

```bash
sqlite3 data/royal_x_checker.db ".backup 'royal_x_checker-backup.db'"
sqlite3 royal_x_checker-backup.db "PRAGMA integrity_check;"
```

Restore by stopping the worker and replacing the configured SQLite database with a verified backup before restarting. Protect backups because they contain Telegram IDs, feedback, credits, and audit history.

For PostgreSQL, use the provider's managed backups or `pg_dump`/`pg_restore`. Remove the `+asyncpg` driver suffix from a SQLAlchemy URL before passing the URL to PostgreSQL command-line tools. Test restoration before relying on a backup. Never upload runtime databases or backups to Git.

## Logging, privacy, and operational limits

- Logs are structured JSON on stdout. The code avoids logging request bodies, bot tokens, Stripe keys, user-supplied file contents, PANs, and CVVs.
- Feedback text is stored for admin review; users are warned not to submit secrets or payment data.
- Public Telegram usernames/names are stored only as ordinary profile display fields; file contents are not persisted.
- The rate limiter/cache are process-local. Use exactly one polling worker unless a shared coordination layer is added.
- Stripe and BIN provider behavior is tested with mocked responses here. Real integrations require a configured, authorized environment and separate sandbox verification.
- The PDF screenshots were not included in the repository, and no external design asset or undocumented reference API was copied.

## Project structure

```text
app/
  main.py                  Async Telegram polling entry point
  config.py                Pydantic Settings and validation
  runtime.py               Service wiring and startup/shutdown
  handlers/                Commands, menus, gates, tools, input, plans, feedback, admin
  keyboards/               Reusable mobile inline keyboards
  services/                Persistence workflows, simulation, fraud, files, credits, plans
  integrations/            Stripe test adapter and BIN metadata adapter
  database/                SQLAlchemy models and async session/migration runner
  utils/                   Temporary export handling
migrations/                Alembic environment and initial schema
scripts/                   Android-friendly owner-ID helper
 tests/                    Async service, provider-mock, security, and UI tests
```

## Verification status

The repository was inspected before changes and contained only a two-line README. Automated tests cover startup migrations, `/start`, navigation/menu callback prefixes, backend admin authorization, profiles/plans, concurrent credit deductions, quotas, simulation/fraud logic, CSV safety/limits, BIN provider errors/cache, Stripe sandbox-disabled behavior and mocked provider responses, and manual activation review.

Run the test suite locally with `python -m pytest -q`. This checkout has no `BOT_TOKEN`, owner deployment IDs, Stripe credentials, or hosted database, so the live Telegram bot and real Stripe/Binlist requests were not started or claimed as verified.
