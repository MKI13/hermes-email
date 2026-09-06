# Reply header continuity

Current package version: 0.44.1.

Incoming `EmailMessage.recipients` now means original To recipients; `cc` is a
separate appended field. The legacy positional constructor remains compatible.
No incoming Bcc data is imported into Reply-All. Additional recipient To/Cc
placement in replies is an explicit policy: primary validated Reply-To/From
in To, other unique non-self recipients in Cc. Invalid, incomplete, over-limit
or self-directed primary routing fails closed instead of silently omitting
recipients or guessing another target.

`reply_to_present` distinguishes a missing field from an empty or unparseable
field; `reply_to_invalid` blocks fallback. Lazy header-parser failures are
caught before the safe message/result boundary. Malformed Reply-To does not
prevent reading the body, but it cannot select a recipient. Multiple From
addresses likewise do not become a guessed reply target.

Reply drafts preserve the single unambiguous RFC parent in `in_reply_to` and
the validated ancestor chain plus parent in `references`. The output subset
uses bounded ASCII dot-atom identifiers. Raw strings, provider-local IDs and
header injection are never emitted as SMTP reply headers. Long chains retain
the root and nearest ancestors, with at most 50 IDs/16384 characters. Outgoing
bytes contain In-Reply-To and References, independent of the subject text.
The behavior follows RFC 5322 section 3.6.4 within this deliberately bounded
syntax subset: https://www.rfc-editor.org/rfc/rfc5322.html#section-3.6.4

Draft schema 2 adds `draft_reply_references` transactionally after strictly
validating schema 1. Existing drafts, recipients, revisions and operation
receipts remain intact. Empty-reference request digests remain byte-compatible
with old operations. Reference changes take part in the mutation digest and
approval snapshot. Unknown schema objects refuse migration; no table is dropped.
Stop/reload old plugin workers before upgrading; old schema-1 code will refuse
the upgraded database. Restore backups consistently, never discard send evidence.

Legacy drafts containing provider-local `in_reply_to` strings stay readable,
but send preparation rejects that field until the user explicitly clears or
corrects it in a new draft revision. It is NOT silently stripped at sending.

Verification covers input parsing, invalid-versus-absent routing, separate
To/Cc, hidden Bcc exclusion, full source -> Reply-All -> SQLite restart -> SMTP
bytes, migrated idempotent receipts, malformed migration rejection and exact
approval invalidation. These changes do not enable model-facing sending.
