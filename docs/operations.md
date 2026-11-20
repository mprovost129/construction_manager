# Heritage 360 operations

## Stability update — November 20, 2026

Render confirmed HTTP health-check timeouts before the 8:49 AM and 9:01 AM
Eastern restarts. Available web-service charts showed memory below approximately
60% and CPU below approximately 30%; they did not establish the underlying
request that blocked readiness. Sentry had no matching issue or trace. Worker
contention during private file downloads is a hypothesis, not a proven cause.

The web container now uses two Gunicorn `gthread` workers with four request
threads each. Eight request slots allow unrelated requests to make progress
while some wait for storage. This does not reserve a dedicated health-check slot
or guarantee availability under arbitrary load. Keep private files behind the
existing authenticated views and retain `private, no-store` responses.

Production database connections now use Django's per-database `CONN_MAX_AGE=60`
and `CONN_HEALTH_CHECKS=True`; the old top-level setting was ineffective. New
connections have a three-second connection timeout. Web queries have a
15-second statement timeout and five-second lock timeout. Management commands
(including startup migrations and imports) do not receive those query limits.
SSL requirements are preserved. Cache connection/read waits are limited to
three seconds and errors are not silently ignored. Existing SMTP timeout is
15 seconds. R2 uses three-second connect and 15-second socket-read timeouts,
two total SDK attempts, and at most two transfer download attempts. Transfers
run without nested thread pools. Socket/statement limits are not total request
or whole-transfer deadlines; multiple operations and retries may take longer.
Timeouts can surface failed requests; do not blindly retry uncertain writes.

`heritage.request_timing` writes warnings for responses taking two seconds or
more (one second for the health route), and for any 5xx response. Fields are
registered route name, standard HTTP method, response status, elapsed milliseconds,
and phase. It never reads user/session data, URLs, URL parameters, headers,
request bodies, or exception text. Unknown routes are labeled `unmatched`.
`phase=response` measures response construction; `phase=stream_close` includes
file transfer and cleanup until the WSGI response closes, not proof of successful
delivery. Logging failures do not affect the request. These diagnostics cannot
record a completion for a killed process and do not replace external monitoring.

After deployment, verify the `gthread` startup log, public `/health/`, login page,
authorized downloads, and Better Stack recovery. Review slow-route warnings and
CPU/memory during normal photo usage before changing hosting plans. The default
Gunicorn access log remains disabled to avoid logging sensitive URL parameters.
Rolling back this release requires no database rollback; it contains no schema
changes. Do not disable health checks to hide timeouts.

## Access and switching

Open `/operations/` from the **Operations** link in the normal application header.
Use **Open app** to return to the project interface. The same signed-in user and
permissions apply in both views. This is not client impersonation.

Active superusers have access. Other developer accounts must be explicitly given
the `core.view_operations` permission ("Can view application operations") through
Django admin's user permissions. Being company administrator or Django staff
alone does not grant access. This permission exposes application-wide counts and
event types, so do not grant it to ordinary Heritage users. A dedicated developer
account with this permission avoids needing superuser privileges for monitoring.

The page is private and sends `Cache-Control: no-store`. It renders without the
database-dependent workspace context processors. Authentication still depends on
the database: a full database outage can prevent login or access to this page.

## What is measured

- A read-only `SELECT 1` query and elapsed time from this process.
- Application environment and `RENDER_GIT_COMMIT`, when provided.
- Active application-owned Sentry client with the expected privacy safeguards.
  This is not confirmation that Sentry received events.
- Last 24 hours of recorded HTTP 5xx responses and project/team notification
  helper outcomes. Acceptance by the email backend is not confirmed delivery.
  Test email backends are labeled separately. Password resets and other direct
  email calls are not covered yet.
- Project, active-account, and document-version counts, plus recent activity
  event types and timestamps. No audit payloads, document contents, recipient
  addresses, request URLs, query parameters, or exception strings are stored
  in operational events or displayed in the feed.

Every page load refreshes the snapshot and displays its check time. Unknown or
missing data must not be interpreted as healthy. Failed telemetry writes emit a
fixed warning and do not interrupt the user's original operation. Events within
a business transaction can be rolled back with that transaction. Process
crashes, failures before middleware, and database outages can leave gaps.

## External health checks and provider links

`GET /health/` returns only `{"status":"ok"}` (200) or
`{"status":"unavailable"}` (503), based on database connectivity. It is public,
uncached, and excludes itself from operational failure recording. Set Render's
health-check path and Better Stack's external monitor to this endpoint once the
accounts are ready. It does not verify migrations, storage, email, or complete
user workflows. External monitoring must remain independent of the app.

Provider dashboard links work without API credentials. Optionally set these
environment values to the team's dashboards, using HTTPS URLs without embedded
credentials, query strings, or fragments:

- `OPERATIONS_RENDER_URL`
- `OPERATIONS_CLOUDFLARE_URL`
- `OPERATIONS_RESEND_URL`
- `OPERATIONS_SENTRY_URL`
- `OPERATIONS_UPTIME_URL`
- `OPERATIONS_GITHUB_URL`

Links alone do not connect a provider or verify its health. This release makes
no external monitoring API calls and does not add service credentials.

## Deployment and retention

Run `python manage.py migrate` and `python manage.py collectstatic --noinput`
with the appropriate deployment settings. Verify developer access, company-user
denial, and switching back to Projects with the same account.

Schedule `python manage.py prune_operational_events` daily through Render once
the account is ready. It removes operational events older than 30 days; use
`--days N` to choose retention and `--dry-run` to preview. The command does not
touch the project audit history. Retention is not automatically scheduled by
this release.

## Agreed infrastructure direction and next connections

- Render: Django, managed PostgreSQL, and background jobs as needed.
- Cloudflare: DNS, Turnstile, and private R2 document storage.
- Resend: application email and future verified delivery/bounce callbacks.
- Sentry: error details and alerts; Better Stack: independent uptime monitoring.
- GitHub: application source, with a future Slater Woods Design Company organization.
- No direct AWS account or AWS console administration is required by this plan.

Private document storage can use Cloudflare R2 with `PRIVATE_STORAGE_BACKEND=r2`.
The Render Blueprint requires the account ID, private bucket name, access key,
and secret key. It fails configuration when these are missing instead of silently
writing production uploads to the temporary server filesystem. File URLs remain
unavailable: authenticated project permissions and applicable client visibility gate
downloads through Django. Public QR document access is retired. Keep the bucket private, with no public R2.dev URL or
public custom domain. The `local` backend remains available for development.

R2 credentials, live upload verification, backup evidence, restore-test tracking,
Resend delivery events, and job heartbeats still require account-side setup.
The Operations page's pending labels remain until those integrations can be
verified. The Blueprint provisions a new database only in the selected workspace;
it does not migrate or delete an existing database.

## Initial Heritage hosting configuration

`render.yaml` prepares a new `heritage360` web service, a 1 GB PostgreSQL database,
and a free internal cache in Ohio. Public database/cache access is disabled.
The primary domain is `https://www.heritage360cm.com`; configure the bare domain
to redirect there and verify both HTTPS certificates before inviting users.
Auto-deploy is off for the initial feature branch. Deploy reviewed commits
manually until the production branch and release process are established.

The deployment uses Resend SMTP with a domain-scoped sending key. The sender is
`Heritage 360 <notifications@heritage360cm.com>` and the default reply address is
`slaterwoodsdesign@gmail.com`. Explicit per-message reply addresses are preserved.
Password resets use the same backend. SMTP acceptance is not delivery evidence.

Complete DNS verification, add secrets directly in Render, create the developer
account, and test login, uploads/downloads, invitation email, password reset,
`/health/`, and a safe Sentry test event before launch. No live production access
or delivery test is implied by this configuration. Turnstile credentials and
legal-page details remain separate launch requirements.

Preserve Heritage's original Buildertrend export outside Git. First inventory
the export and map original IDs, relationships, attachments, and history. Test
one representative project before a wider import. Add reconciliation counts,
duplicate detection, and missing-attachment reporting to Operations with the
importer. Do not report imports or backups as complete without recorded evidence.


## Deadline reminders and punch lists (2026-11-20)

Punch lists are project-scoped and internal by default. Project managers with
management access create/edit/share/cancel items and verify completed work.
Assigned active staff can start work, submit it for verification, and add photos.
Primary clients can read only explicitly shared items/photos; internal notes,
assignees and verifier identities are omitted. Client preview uses the same
visibility checks and remains read-only. Photo removal revokes access but retains
stored bytes under the existing backup retention policy. The document backup
inventories the whole private bucket, including the new punch_photos prefix.

Selection deadlines can be adjusted on draft/open selections without changing
prices or choices. Both selections and punch items have an opt-out checkbox for
scheduled reminders. Paused/completed projects and closed items are skipped.
Selection reminders go to active primary clients; punch reminders go only to the
currently assigned active staff member with project access. Their existing
email/in-app preferences apply. Each email has a single recipient.

Run `python manage.py send_deadline_reminders --dry-run` to count eligible
recipient reminders without writes or emails. The real command schedules 3 days
before, 1 day before, due day, 1 day overdue, then weekly. A durable unique
recipient/item/date claim suppresses duplicate manual/scheduled attempts, even
across concurrent runs. In-app alerts and the claim commit together before email.
An unconfirmed SMTP attempt is not automatically retried that day (acceptance
may be ambiguous); in-app alerts remain. A failed email causes the scheduled
command to exit nonzero and records the existing email-failure operations signal.
A crash between claiming and email submission leaves a pending record; inspect
DeadlineReminder status before any manual recovery to avoid duplicate mail.
The legacy `send_overdue_selection_reminders` command remains overdue-only and
shares the same daily deduplication; do not schedule both commands.

Optional deployment: `render-reminders.yaml` creates only
`heritage360-deadline-reminders`, using the existing private database and SMTP
credentials through Render references. It uses no R2, QuickBooks, or web-session
secret. `Dockerfile.reminders` has no HTTP listener or migration/bootstrap step;
startup checks that migrations are current. Apply only after web migrations are
live, and deploy the scheduler with future reminder/model changes. Schedule:
every five minutes for staff mail; daily deadlines start at 14:00 UTC (9am EST /
10am EDT) with duplicate suppression. Cost: minimum $1/month plus usage, confirmed
in Render documentation on 2026-11-20. Obtain approval for this new recurring
service before creating it. Keep the existing backup cron unchanged.

Before activation, run the dry-run against production and verify delivery
settings without exposing credentials. After activation, inspect the first run,
mail-provider acceptance and receipt counts. The feature is not automatically
scheduled merely because the web release is deployed. Rollback the web code if
needed without reversing additive migrations or deleting punch-list data;
suspend the reminder cron separately to stop scheduled delivery.

## Staff messaging delivery

StaffThread/Participant/Message are separate from client conversations. Every
request checks active internal company membership, participation and (when
linked) internal project access. Client preview cannot enter this area. In-app
notification queries recheck that access; queued email rechecks immediately
before sending. Audit events contain no subject or message text.

Messages, in-app alerts and StaffMessageDelivery rows commit atomically. UUID
submission keys prevent repeated form submissions from duplicating a message.
SMTP never runs in the web request. `process_notifications` claims up to 50 pending
deliveries per run and sends separate plain-text emails with authenticated links.
Per-thread email opt-outs apply to pending and future mail. SMTP acceptance is
recorded as accepted, which does not guarantee final inbox delivery.

Deploy web migrations 0046/0047 first. Then deploy the existing reminder cron with
Dockerfile.reminders and change its schedule to `*/5 * * * *`. This reuses the
approved service and existing database/mail references; no new worker is needed.
The command processes staff email each tick and checks daily deadline reminders
after 14:00 UTC. Existing daily claims suppress duplicate deadline notifications.
Do not add a second deadline schedule. Inspect the first combined run and verify
its counts; logs must not contain message bodies, recipients or credentials.

Failed SMTP attempts and sending claims older than 15 minutes become failed and
raise an operations email-failure signal plus a failed cron run. They are not
automatically retried because SMTP may already have accepted the message. Inspect
the provider's delivery history before any operator recovery; never blindly
reset failed/sending rows. A crash before claiming leaves mail pending for the
next run. In-app messages remain available regardless of email delivery status.
Queue size over 50 per tick can delay email; monitor pending counts and oldest
created_at when investigating delays. Suspend the cron to stop background mail;
retain additive migrations and conversation data during a code rollback.
