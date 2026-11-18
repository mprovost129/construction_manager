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

- Add and enroll administrator MFA; the app currently relies on passwords plus login throttling and CAPTCHA.
- Review production account access, hosting/GitHub/Cloudflare MFA, R2 public-access configuration, database network restrictions, backup restore procedures, and dependency vulnerability reports. Source configuration and unit tests do not attest to all live provider settings.
- Arrange an independent penetration test before broad financial-data rollout.
- Keep QuickBooks disconnected until edition/year, the Windows connector computer, company-file identity, and account mappings are validated. The user reports the company file is on the network. Use an authorized Windows host with access to that file; do not expose SMB or the company file to the public internet.

References: [Cloudflare server validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/), [OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [Intuit Web Connector setup](https://quickbooks.intuit.com/learn-support/en-us/help-article/install-products/set-quickbooks-web-connector/L4Vp7VI44_US_en_US).
