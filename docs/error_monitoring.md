# Production error monitoring

Construction Manager uses Sentry for production exception reporting and sampled
performance traces. Monitoring remains disabled until `SENTRY_DSN` is present in
the Render service environment.

## Sentry project setup

1. Create a Sentry organization owned by Slater Woods Design Company and a
   Python/Django project for Construction Manager.
2. Copy the project's DSN into Render as `SENTRY_DSN`.
3. Keep `SENTRY_TRACES_SAMPLE_RATE=0.05` for the pilot. Increase it only when a
   specific performance investigation needs more data.
4. In Sentry, keep server-side data scrubbing enabled and turn on spike
   protection.
5. Create an issue alert for the first occurrence of a new error and for error
   regressions. Deliver alerts to a monitored Slater Woods email address.
6. Deploy, send one test event from a Render shell, and confirm its environment,
   release, stack trace, and alert delivery before relying on monitoring.

## Privacy defaults

The application sends errors and 5 percent of performance traces. It does not
send authenticated user identity, cookies, request headers, request or response
bodies, URL query parameters, database query values, or local stack variables.
Before sending errors or traces, a final recursive scrubber also removes email
addresses, UUIDs such as invitation tokens, sensitive query values, and database
exception detail text that may contain record values. These restrictions protect
client emails, invitation tokens, signature records, and document metadata. Do
not weaken them without a documented privacy review.

For database exceptions, the event keeps the exception type and stack trace but
removes the exception detail, message, log entry, extra context, and breadcrumbs.
This intentionally trades some diagnostic context for stronger protection of
record values that database drivers may repeat across several event fields.
When exceptions are chained, every exception message in the chain is removed if
any member is a database exception, preventing wrapper messages from repeating
the database detail.

The Render-provided `RENDER_GIT_COMMIT` value is recorded as the Sentry release,
so an error can be tied to the exact deployed commit without another secret or
manual release setting.

Production management commands select the production settings whenever
`APP_ENVIRONMENT=production`, even if `DJANGO_SETTINGS_MODULE` is not supplied
explicitly. A missing DSN disables monitoring, and a malformed DSN is logged and
ignored so an observability configuration mistake cannot prevent the app from
starting. An invalid trace sample rate is also logged and falls back to zero
performance traces while error reporting remains available.
Production management commands mark themselves before Django imports settings,
allowing production settings to initialize the global client exactly once with
the uncaught-exception hook disabled. The command wrapper captures and flushes
failures explicitly, preventing duplicate events. If settings or Django fail to
import before that initialization completes, the wrapper installs the same full
privacy configuration immediately before capturing the startup failure. Django's
management module import runs inside this wrapper as well.

After successful setup, the application blocks later calls to the public Sentry
initialization API. This prevents another component from replacing the client
with weaker collection settings during runtime. Management-command failures are
sent only after the active client and its privacy options have been verified;
monitoring setup errors never replace the original command failure.
