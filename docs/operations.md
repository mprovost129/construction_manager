# Heritage 360 operations

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
