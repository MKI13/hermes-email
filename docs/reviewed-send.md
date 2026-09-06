# Human-reviewed send workflow

Current package version: 0.44.1.

The complete workflow is exposed as an explicit **local Hermes terminal command**,
not a model tool or gateway slash command:

```text
hermes -p <profile> email-send <draft_id>
```

The operator must opt in with `send_workflow.mode: local-test`, configure a
literal loopback SMTP endpoint, local drafts, an exact productive profile owner,
fixed SMTP sender, recipient policy and `safety.allow_send: true`. All recipients
must use reserved `.invalid` domains in local-test mode. Default mode is disabled;
installation/registration does not open SMTP or resolve credentials. No existing
profile is silently armed. Production transports still require TLS; plaintext
SMTP exists only in injected loopback test code, never as a configuration mode.

The terminal shows the explicit SEND action, full subject/body, sender and all
To/Cc/Bcc recipients as escaped JSON. A fresh challenge must be answered exactly.
Review-only grants, stale revisions, changed configuration or identity, expired
approvals and noninteractive commands cannot authorize a new attempt.

After consent the workflow reserves the exact draft revision with a SQLite write
reservation, rechecks the immutable snapshot, consumes the one-use approval,
constructs stable message bytes, commits the mandatory send intent, and makes
one synchronous SMTP attempt. Concurrent draft mutations cannot change the
reviewed content during submission. Identity/configuration/expiry are rechecked
at transport entry and immediately before production SMTP DATA. No model policy
or prior session-wide approval bypasses this sequence.

The deterministic operation ID identifies the profile/account/draft/revision.
Existing records return their recorded status without another prompt, secret
lookup or transport call. A failure/uncertain attempt is never automatically
resubmitted; changing a draft deliberately is a new revision requiring new review.
The workflow does not infer permission to make a replacement draft after failure.

## Results and copies

`smtp_accepted` means server acceptance, NOT end-to-end delivery. The response
always leaves `delivery_confirmed: false`. Any uncertain transport/local commit
outcome carries `automatic_retry_forbidden: true` and requires manual review.
The ledger retains the prior attempt across restart.

Optional `save_local_copy: true` stores the exact non-Bcc-header wire bytes in
private `sent-copies/<operation-hash>.eml` files. This is a LOCAL copy, not proof of
provider Sent-folder storage. A copy failure is a separate warning and never
retries SMTP or hides a known acceptance. Existing copies are never overwritten
with different data. Copy count is bounded; no send evidence is pruned.

No outgoing attachment feature is added here. Real account/provider validation
is a separate roadmap step. Gateway/Telegram send approval remains unavailable;
local OS identity is not an isolation boundary against malicious same-user code
or automated PTY control. See trusted-approval.md for that explicit threat model.

## Verification

Deterministic tests cover the official Hermes CLI registration/handler path,
absence of model/slash send surfaces, exact consent, mutation and configuration
changes during review, expiry, concurrent updates, local SMTP DATA capture,
restart/idempotency, accepted/failed/unknown results, failed post-SMTP persistence
and failed sent-copy storage. No external mailbox or model API is used.

## Separately authorized account testing

Version 0.43.0 additionally supports `account-test` with an exact-address-only
allowlist; domain-wide and unrestricted recipient policies are rejected. Defaults
remain disabled and no real account is activated by installation. The local terminal
is still the only supported approval surface. See provider-validation.md for the
independent account test and the still-open authenticated Proton test-account gate.
