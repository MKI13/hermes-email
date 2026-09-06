# Multi-folder search and conversation context

Current package version: 0.44.0.

## Enable only explicitly chosen mailboxes

Configure `imap.mailboxes` with one to eight exact mailbox names and an explicit
`imap.account_namespace`. Include `imap.mailbox` (the primary folder, normally
INBOX). Names are not patterns: the plugin neither lists all folders nor expands
wildcards. Names with spaces, quotes or backslashes are quoted as one IMAP
argument. This release accepts printable ASCII names; non-ASCII mailbox naming
requires a future explicit encoding implementation.

```yaml
hermes:
  profile: email
email:
  provider: imap
  read_mode: readonly
imap:
  host: mail.example.com
  port: 993
  security: tls
  username_ref: HERMES_EMAIL_IMAP_USERNAME
  password_ref: HERMES_EMAIL_IMAP_PASSWORD
  account_namespace: primary-mail-account
  mailbox: INBOX
  mailboxes: [INBOX, "Sent Items", Archive]
safety:
  allow_send: false
  allow_delete: false
  allow_move: false
```

These are examples, not guessed provider folder names. Select the names exposed
by your mail provider. For single-profile Hermes, bind `hermes.profile: default`
instead of creating another profile. Settings belong under
`plugins.entries.hermes-email.settings`, as in [installation.md](installation.md).
No EF-Sinn names or account settings are required.

## Compatibility and identity

With `mailboxes: []` (default), the existing single-folder provider and its legacy
`imap-v1` identifiers remain unchanged. To use scoped IDs even for a single
folder, explicitly set `mailboxes: [INBOX]` and `account_namespace`.

Multi-folder IDs bind the configured account namespace, endpoint, username
reference, exact mailbox name, UIDVALIDITY and UID. They remain stable across
runtime reloads while those identities remain unchanged. Identical UIDs in two
folders do not identify the same message. Renaming a folder changes its identity.
A foreign account ID, unconfigured folder ID or legacy `imap-v1` ID is rejected
before credential lookup; there is no guessed migration to a mailbox.

The namespace is operator-owned. When repointing a secret reference to a
DIFFERENT account, choose a new namespace. A hash of configuration cannot detect
an account secretly replaced behind the same reference. Secret values are never
read to construct identifiers or cursors. These IDs locate data; they confer no
read/write/consent authority and are not proof of sender identity.

## What list and search actually do

The existing `email_list_messages` and `email_search_messages` tools visit bounded
UID windows in the configured folders. They fetch only `BODY.PEEK[HEADER]`, not
full messages, and do not set Seen flags. Search matches normalized subject,
From, To, Cc and Reply-To addresses/display names. **This is header search, not
full-text body search.** Bcc is not searched or inferred from hidden recipients.

`limit` is a total UID-slot inspection budget, not a requested number of matches.
Each call visits a folder at most once, with fair rotation when the budget is
smaller than the folder count. Gaps left by deleted messages can yield zero
matches with a non-null continuation cursor. Do not silently loop to fill a page.
Results are folder-window ordered, not a globally newest-first mailbox merge.

Each result includes `folder_scan`: configured folders and their states
(`not-scanned`, `partial`, `complete`, `unavailable`), scan completeness,
header completeness, messages scanned, and the requested UID budget.
`scan_complete` applies to the bounded per-folder snapshots inspected since the
start of that cursor chain, not future incoming mail or unconfigured folders.
The folders do **not** form one atomic cross-folder snapshot. Truncated headers
and bounded normalization are conservatively reported as incomplete.

A missing or inaccessible mailbox remains `unavailable`, even if other folders
were exhausted and `next_cursor` is null. Start a new scan after repairing access.
Authentication, TLS, connection and protocol failures are not hidden as successful
partial results. No error text, host credentials or certificate values are placed
in coverage output.

## Continuation cursor rules

Cursors are authenticated with a process-local random key. They bind the exact
account/folder set, search query (or list operation), rotation and progress.
Changing the query, reusing a search cursor for listing, altering a cursor or
reloading the provider invalidates it before network/credential access. Start a
new scan after reload. Cursors are bounded to 512 characters and never written to
plugin storage. Message IDs, unlike these cursors, remain restart-stable.

## Conversation bodies and limits

`email_get_thread` reads its seed and scans at most `scan_limit` UID slots across
the selected folders. It links only RFC Message-ID/In-Reply-To/References, never
similar subjects, names or text. Only selected linked messages are then fetched
for body detail; unrelated bodies and attachments are not downloaded for search.

Each header is at most 16 KiB, and total header payload is bounded by
`imap.max_page_bytes`. The selected non-seed body reads have a separate aggregate
`max_page_bytes` budget. The seed is bounded by `max_message_bytes`. Consequently
the payload bound per thread invocation is at most
`max_message_bytes + 2 * max_page_bytes`, apart from protocol framing. The tool's
body output window can be smaller still. Raw message partial reads can include
attachment bytes within that fixed bound but never expose/download attachment
content as a separate operation.

A message that disappears during body lookup increments `missing_messages` and
makes the scan incomplete. Oversized bodies retain `source_truncated`; limiting
the returned thread retains `thread_truncated`. Missing reference counts remain
visible. Duplicate RFC IDs in different folders retain their separate physical
locations rather than silently discarding or merging content. Header relationships
are untrusted data, not authentication or permission to reply/send.

## Verification

`tests/test_multi_mailbox.py` exercises scope collisions, stale UIDVALIDITY,
modified/reloaded/query-mismatched cursors, sparse ranges, signed malformed
state, byte limits, missing folders, closed providers, headers-only requests,
mailbox quoting, selected-body hydration and official Hermes registry dispatch.

`scripts/multi_folder_e2e.py` additionally creates synthetic Inbox/Sent/Archive
fixtures on the pinned independent GreenMail server and invokes the real plugin
IMAP clients and tools. Fixture creation uses IMAP writes solely on disposable
local test accounts; the plugin path itself is read-only. The test checks Seen
flags and never uses an AI model, a real account or external delivery.

The explicit Proton test-account gate from step 6 is still open. No production
profile is configured by these tests. Protocol background: RFC 9051 sections
2.3.1.1, 6.3.3 and 6.4.5; Python imaplib is the transport implementation.

The installation scan regression also includes the cursor and audit test files.
Test-only invalid cursor strings do not point to host credential/system files;
normal imports are used in storage tests. The community scanner remains enabled.
