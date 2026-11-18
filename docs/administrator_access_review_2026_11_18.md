# Administrator access and key recovery review — November 18, 2026

## Live results

This review ran application permission checks and authenticator decryption checks
inside a PostgreSQL read-only transaction on the deployed application. It did not
change accounts, memberships, invitations, keys, recovery codes or session data.

| Account | Observed state |
| --- | --- |
| Owner/developer administrator | Active company administrator and Django superuser; two-step enrollment confirmed; an unverified test session was redirected to verification |
| Ryan DuVally | Active company administrator; not enrolled in two-step verification; an unverified test session was redirected to setup; no Operations or Django staff/superuser access |
| Michael Provost sample client | Only project 1 visible; no internal company access, internal project budgets, Operations, Django staff or superuser permissions |
| Mike Mullen | Administrator invitation pending; no corresponding active account in the three-account audit |
| Anonymous visitor | No visible projects or Operations access through the checked permission functions |

Both administrator accounts have underlying access to projects 1, 4 and 5 as
intended by their roles. The MFA middleware prevents an unverified administrator
from reaching those projects. Ryan must complete his own enrollment; no setup
secret or recovery code was generated on his behalf. Mike Mullen must accept the
existing invitation and complete enrollment. No messages or replacement
invitations were sent during this review.

Production administrator MFA and browser CAPTCHA settings were enabled. This
checks deployed settings and application functions, not every HTTP endpoint or
provider security control. Earlier client-isolation tests and the successful
live CAPTCHA test remain separate evidence; this is not a penetration test.

## Encryption-key status

- The application encryption key is configured in the running service.
- The one stored authenticator seed was readable with the configured key set;
  no unreadable seeds were found. Only the result was output, never the seed,
  encryption key, password or recovery-code value.
- No historical fallback keys are currently configured.
- **Owner confirmed the recovery copy saved on November 18, 2026.** After
  instructions to copy the current `SECRET_KEY` into a protected Google Password
  Manager note saved to the Google Account, the owner replied "saved". This is
  owner-confirmed custody; the assistant did not inspect the vault or compare
  secret values. Recovery using that saved copy has not been tested.

## Private recovery-copy procedure

1. Choose an approved private password manager or encrypted recovery vault.
   Do not put the actual secret in this repository, this report, ordinary cloud
   documents, an unencrypted text file, chat or a support ticket.
2. In the existing Heritage production service's Render Environment page, copy
   the current `SECRET_KEY` directly into a secure vault item. This is a copy-only
   operation: do not edit or regenerate the live value. The owner performs this
   step privately; the assistant does not operate the password manager.
3. Label the item **Heritage 360 — production application recovery key**, and
   record the environment, source service, date saved and the backup dates it
   applies to. Confirm privately that the saved value matches the live value.
4. Preserve historical key versions if the application key is rotated later.
   Keep each key for as long as retained backups may contain data encrypted with
   it. Follow the [MFA key-rotation procedure](administrator_mfa.md).
5. Verify the owner can recover access to the vault if the phone or hosting
   account becomes unavailable. Confirm completion without sharing secret values.

The [successful database/document restore drill](restore_drill_2026_11_18.md)
did not copy application encryption keys or test login against the restored
database. Key custody is now owner-confirmed. Recovery using the saved key and an
application-level recovery check remain separate requirements before treating
full disaster recovery as verified.
