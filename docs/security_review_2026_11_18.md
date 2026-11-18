# Heritage security review — November 18, 2026

This is an application access-control review and hardening change, not a penetration-test certification or a guarantee against intrusion.

## Changes

- Retired anonymous document pages/downloads and the public-share toggle. Previously enabled QR tokens now return 404, including tokens verified in an older session. Documents require authenticated project access and applicable client visibility.
- New staff document uploads default to internal-only. Existing client-visible documents retain their settings; client uploads retain their intended project-sharing workflow.
- Added production-default Cloudflare Turnstile verification to all browser mutations: login, password reset, invitation acceptance, uploads, decisions, and administrative forms. Server validation rejects absent/invalid/expired tokens, hostname/action mismatches, outages, and malformed responses. Database uniqueness prevents local token replay even if Cloudflare briefly accepts a duplicate. Only SHA-256 fingerprints are stored, expiring after ten minutes and pruned on subsequent successful verifications.
- Only the exact QuickBooks Desktop protocol and signed Stripe webhook routes are exempt. Their existing authentication and disabled feature gates remain. GET/HEAD/OPTIONS requests, including search filters, do not require CAPTCHA. No exemption is based on user-supplied headers, content type, or URL prefixes.
- Added private/no-store response headers where not already supplied, and same-origin referrer policy. Static assets retain their existing caching behavior.
- Preserve submit-button values, browser validation, upload contents, and user text when the browser challenge fails. Django CSRF checks remain in force.

## Validation

Automated tests exercise authentication, client and signer privacy, unassigned/revoked users, internal downloads, imported history restrictions, retired public access, token failure/replay cases, admin login, and multipart uploads. Local browser checks use a synthetic Turnstile substitute; they do not prove a production Cloudflare challenge succeeds. Verify a normal production form submission with the user after rollout.

## Outstanding security work

- Administrator MFA is implemented in the follow-up release; each account owner must enroll and retain recovery codes. See [administrator MFA](administrator_mfa.md). Enrolled users require MFA; production administrators cannot access business pages before enrollment.
- Finish provider-account MFA enrollment, GitHub access/MFA review, and backup restore drills. Continue periodic dependency vulnerability checks. Source configuration and unit tests do not attest to all live provider settings.
- Arrange an independent penetration test before broad financial-data rollout.
- Keep QuickBooks disconnected until edition/year, the Windows connector computer, company-file identity, and account mappings are validated. The user reports the company file is on the network. Use an authorized Windows host with access to that file; do not expose SMB or the company file to the public internet.

References: [Cloudflare server validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/), [OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [Intuit Web Connector setup](https://quickbooks.intuit.com/learn-support/en-us/help-article/install-products/set-quickbooks-web-connector/L4Vp7VI44_US_en_US).

## Follow-up live infrastructure review

Observed November 18, 2026 in the authenticated provider dashboards:

- PostgreSQL reports all internet traffic blocked by its database inbound rules.
  The broader workspace network rule is permissive, but the database-specific
  effective policy blocks external connections. Do not relax that database rule.
- Database recovery offers a three-day point-in-time recovery window and two
  existing export records. The initially loading UI briefly displayed a generic
  seven-day message; the settled account-specific view showed three days and a
  paid upgrade for seven. No restore or plan change was performed.
- Both the primary document bucket and backup bucket report public access disabled.
  The backup bucket has no public development URL or custom domain and has an
  enabled indefinite bucket-lock rule protecting backup history.
- The scheduled document backup at 4:00 AM EST completed successfully, with the
  job reporting seven verified objects, four new copies, and an accepted completion
  heartbeat. Documents uploaded later that morning are outside that snapshot.
- Render account two-factor authentication was Disabled; Cloudflare authentication
  was Inactive. Owner enrollment was handed off. Do not treat either as enabled
  without subsequently observing the completed state.

These checks verify settings and backup-job evidence, not a full restore. A safe
isolated restore drill and provider recovery-code custody remain outstanding.

## Dependency security follow-up

The November 18 pip-audit check identified advisories in eight pinned libraries.
Its raw count included duplicate advisory records and aliases; it is not a count
of independently exploitable application vulnerabilities. Updated Django to
6.0.10, Pillow to 13.0.0, filelock to 4.2.12, pytest to 9.0.3, sqlparse to 0.6.0,
urllib3 to 2.8.0, virtualenv to 21.14.10, and Werkzeug to 3.1.9. The virtualenv
update also required compatible distlib, packaging, and python-discovery versions.

Pinned the remaining six resolved supporting libraries to the versions tested.
All 56 resolved packages matched the test environment. Both the patched declared
requirements and the complete resolved package list reported no known advisories
through pip-audit/PyPI on November 18, 2026. The full suite passed: 524 tests and
67 subtests, with dependency consistency, lint, and migration checks passing.
HTTPS verification remained enabled; the advisory client used Windows' trusted
certificate store because its isolated certificate bundle did not recognize the
local issuer. No application or financial data was sent to the advisory service.

The scan covers Python package advisories, not the operating-system image,
unpublished vulnerabilities, exploitability analysis, or an independent pentest.
GitHub security settings required a fresh login and were not verified.

Primary release references: [Django November security release](https://www.djangoproject.com/weblog/2026/nov/03/security-releases/),
[Pillow 13.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html).
