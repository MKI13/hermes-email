# Provider/account validation

Current package version: 0.44.1.

## Evidence and remaining gate

Roadmap step 6 is **partially complete**, not a blanket production-account PASS.

| Scope | Evidence |
| --- | --- |
| Independent GreenMail 2.1.13 account | Real TLS SMTP AUTH, IMAP LOGIN/EXAMINE/fetch, reply headers, reviewed workflow, local sent copy, recipient receipt and no duplicate on replay |
| Wrong test password | Expected typed authentication failure; no successful account access claimed |
| Wrong certificate pin | Denied before credential lookup, for IMAP and SMTP |
| Actual local Proton Bridge transport | IMAP and SMTP STARTTLS certificate pin verified without supplying credentials; no LOGIN/AUTH, mailbox read or SMTP DATA |
| Authenticated Proton test account | **NOT TESTED: a separately authorized test account and explicit test recipients are not configured** |
| Gateway human approval | Not supported by this release; only the local foreground-terminal surface is implemented |
| Production mailbox deployment | Not enabled or validated by these tests |

A transport-negative result is not proof that an account works. Do not change the
Proton account row to PASS without login, a consented test-message read and a
separately confirmed test send with recipient verification. Never use production
credentials just because they are present elsewhere on a host.

## Independent server test

`scripts/greenmail_e2e.py` uses an immutable GreenMail image digest, a private
Docker internal network, a read-only container, dropped capabilities, resource
limits and loopback-only TLS-transparent forwarding. It creates random synthetic
credentials, no production mounts, and only `example.invalid` mailboxes. It removes
its own container/network/forwarders after completion, including failures.

The script uses the **real production IMAP/SMTP clients**, not a mock provider.
The review presenter is deliberately synthetic test code; it is not a claim of
physical human participation. The separate terminal and official Hermes CLI tests
exercise the local approval adapter. No model is called. GreenMail's in-memory
mail storage is not a test of persistent Proton mailbox recovery.

Run deliberately on a host with Docker and OpenSSL:

```sh
docker pull greenmail/standalone@sha256:3df66b7edd01c8a301343ca5e3601d8674760d4708655573560c24745e624fb2
python scripts/greenmail_e2e.py
```

The CI includes an independent job running this same account test. The certificate
pin for this synthetic, newly-created sandbox is discovered by the **test harness**;
production runtime never discovers or silently trusts a new certificate.

## Transport compatibility

Remote mail servers retain normal hostname/CA-verified TLS. IMAP `tls` and SMTP
`implicit_tls` / `starttls` remain available. There is never a plaintext fallback.

For a local mail bridge only, use an explicitly verified certificate fingerprint:

- IMAP: `starttls-pinned` or `tls-pinned`.
- SMTP: `starttls-pinned` or `implicit_tls_pinned`.
- Host must be the literal `127.0.0.1` or `::1`; `localhost` is not a pinning target.
- Fingerprint is exactly 64 lowercase SHA-256 hex characters, quoted in YAML.

These modes replace CA/hostname validation with an exact peer-certificate pin;
they are not unverified TLS. TLS 1.2 or later is still required. Obtain the pin
from the operator-controlled Bridge certificate through an independent trusted
route, and review rotation manually. A changed pin changes the approval snapshot.

IMAP authentication is selected once from capabilities **after verified TLS**.
Use advertised `AUTH=PLAIN`; otherwise use standard LOGIN only when LOGIN is not
disabled. LOGIN arguments are quoted and control/non-ASCII credentials are denied
on that path. A failed PLAIN attempt never falls back to LOGIN. A provider needing
OAuth-only authentication still needs a separate OAuth implementation.

## Deliberate account test mode

All defaults remain disabled. `send_workflow.mode: local-test` additionally
restricts the host to literal loopback and recipients to `.invalid` domains.

`send_workflow.mode: account-test` supports a separately authorized real test
account, but requires **only exact recipient addresses** in the allowlist. Domain
allowlists and `recipient_policy.mode: all` are rejected in this mode. This setting
is not consent: every new attempt still requires foreground-terminal review,
full snapshot binding, valid expiry and the mandatory send-intent ledger.

Configure only the selected test profile under the official plugin settings:

```yaml
hermes:
  profile: email-test
# Supply this profile's own validated IMAP, SMTP and draft settings separately.
send_workflow:
  mode: account-test
  save_local_copy: true
recipient_policy:
  mode: allowlist
  allowed_addresses:
    - explicitly-approved-test-recipient@example.com
safety:
  allow_send: true
  allow_delete: false
  allow_move: false
```

The address is a placeholder, not permission to contact anyone. Keep the mode
`disabled` until the user supplies a dedicated test account and an actual approved
test recipient. Do not paste passwords into chat, commits, PRs or reports; place
only this test account's secret values in its secure local secret configuration.
No production-profile configuration is copied into the test profile.

The workflow stores a local `.eml` copy only after SMTP acceptance; it does not
APPEND to a provider Sent mailbox. A copy failure never permits another SMTP
attempt. `smtp_accepted` is not proof of end-to-end delivery; the independent lab
checks recipient receipt separately.

## Sources and scope

GreenMail's upstream project and container documentation:
https://greenmail-mail-test.github.io/greenmail/ . Python protocol clients:
https://docs.python.org/3/library/imaplib.html and
https://docs.python.org/3/library/smtplib.html . These sources describe the
underlying tools, not an external audit of Hermes Email.
