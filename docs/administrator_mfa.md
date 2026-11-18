# Heritage administrator two-step verification

Production requires authenticator enrollment for active company administrators,
Django staff/superusers, and accounts permitted to view Operations. Other users
can opt in under Account security. Once enrolled, every account must verify,
regardless of the administrator rollout setting. MFA does not grant any project,
financial, company, or Operations permission.

## Enrollment and daily use

After password sign-in, an administrator without an authenticator is restricted
to enrollment and sign-out. Existing sessions are gated as soon as this release
is deployed. Enter the current password to start a ten-minute setup, scan the
locally generated QR code with an authenticator app, then confirm a six-digit
code. The account owner must perform enrollment themselves; do not send QR codes,
setup keys, passwords, or recovery codes in chat or support tickets.

Ten random 128-bit recovery codes are shown once. Save them in a protected password
manager or an offline secure location separate from the phone. Only SHA-256 hashes
are stored. Recovery requires a password-authenticated session and consumes a code
atomically. Replacing recovery codes requires the current password plus a fresh
authenticator code or an unused recovery code. Previous recovery codes are revoked.

Successful verification rotates the session identifier and lasts at most eight
hours. Logout, password/session invalidation, or device removal requires renewed
verification. A password reset does not bypass MFA. An expired verification blocks
mutating requests without applying changes; verify and submit the form again.

No browser interface can disable MFA, expose an enrolled seed, or replace the
authenticator. Phone replacement and loss of both factors require an operator-led
identity verification and controlled recovery. Verify identity through established
company channels, record approval, invalidate all account sessions, then arrange
fresh enrollment through the restricted setup flow. Do not remove MFA solely on
the strength of an email, chat request, or password reset. This release does not
automate that exceptional recovery operation.

## Security implementation

- django-otp 1.7.5 supplies TOTP verification, monotonic timestep replay protection,
  and exponential throttling. Verification locks the device in a database transaction.
- Authenticator seeds are encrypted with Fernet under a purpose-separated key
  derived from the application SECRET_KEY. The parent OTP table's plaintext key
  is blank. Database-only disclosure does not reveal authenticator seeds. Protect
  the application key independently from database backups; app-server compromise
  can still expose it.
- Initial setup requires password reauthentication and is bound to one session.
  A user-row lock and one-to-one account relation serialize enrollment.
- TOTP and recovery attempts share throttling. Device IDs and enrollment flags
  supplied in requests cannot select another person's authenticator.
- The middleware guards direct reads, downloads, admin, and mutations, including
  existing password-authenticated sessions. Machine-protocol exceptions are exact
  view names and retain their independent authentication/disabled feature gates.
- Setup/verification/error pages omit workspace navigation, notifications, and
  previous workspace messages. Responses are private/no-store. Cloudflare and CSRF
  checks also cover MFA forms. QR codes never go to an external image service.
- Django admin does not expose OTP models. Enrollment, verification, recovery
  use, and recovery replacement events are recorded without codes or seeds.

## Deployment and key rotation

Install pinned requirements and apply all migrations before accepting traffic.
`MFA_REQUIRED_FOR_ADMINS` defaults to true in production. Disabling this setting
does not bypass already-enrolled accounts, but would leave unenrolled admins
unprotected; do not use it as a routine recovery mechanism.

Before rotating SECRET_KEY, put the old key in SECRET_KEY_FALLBACKS through the
secret manager. Deploy the new current key with the old fallback retained. Run
`python manage.py rewrap_mfa_keys` to verify readability without changing data,
then run with `--apply` to re-encrypt all seeds atomically with the current key.
No key material is printed. Verify enrollment/sign-in before removing fallbacks.
Retain the appropriate historical key in a protected recovery vault for old
database backups; restoring encrypted seeds without their key will prevent MFA.

## Validation and limits

Automated checks cover enrollment expiry, foreign sessions/devices, encrypted
storage and rotation, password reauthentication, direct/admin access, role changes,
client authorization after MFA, session expiry, TOTP replay, recovery reuse,
throttling, CSRF, and CAPTCHA. Live enrollment still requires each account owner
to finish setup and save their codes. This is not a penetration-test certification.

TOTP improves password-only access but is not phishing-resistant. A later passkey
or hardware-key rollout is appropriate for highly privileged accounts.
