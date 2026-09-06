# Stable ten-step production-readiness roadmap

Current package version: 0.42.0. These numbers remain stable when tasks are
requested by number; version numbers are not roadmap step numbers.

1. **Send-ledger storage hardening** — implemented in 0.38.0; private creation,
   strict schema/identity checks, transactional migration, limits without pruning.
2. **Cross-process dispatch ownership and recovery** — implemented in 0.39.0;
   OS lease spans the full synchronous transport attempt, live owners are preserved,
   orphan recovery requires exclusive lease acquisition. Stable Date/Message-ID
   and exact request digests prevent regenerated retry data.

**Next requested step: 6.** Steps 1 and 2 have dedicated releases and CI evidence;
see their pull requests and release notes. The pending steps below are not implemented.

3. **Authenticated Hermes user confirmation** — implemented for the local OS-authenticated
   foreground-terminal surface in 0.40.0; exact snapshot/account/profile/principal/session
   binding, expiry and single use. Gateway adapters remain disabled; see trusted-approval.md.
4. **End-to-end reply headers and recipient model** — implemented in 0.41.0;
   separate To/Cc, invalid-vs-absent Reply-To and persisted In-Reply-To/References
   through outgoing bytes. Legacy drafts migrate transactionally; see reply-headers.md.
5. **Connect the complete user-approved send workflow** — implemented in 0.42.0
   as opt-in local CLI workflow with full human review, pinned draft revision,
   durable intent, local SMTP and separate sent-copy warnings. Model/gateway sending
   remains disabled; see reviewed-send.md.
6. **Complete provider/account integration** — pending; authorized test account,
   Proton Bridge and independent server; transport-negative tests are not login PASS.
7. **Multi-folder search and conversation context** — pending; bound account and
   mailbox identities, Inbox/Sent/Archive with explicit incompleteness reporting.
8. **Isolated attachment analysis** — pending; explicit request, bounded extraction,
   no network, macros or executable content; output remains untrusted.
9. **Local mail work queue** — pending; reviewed classifications, actionable states,
   reversible corrections, no automatic mailbox mutations.
10. **Universal setup, upgrades and recovery** — pending; existing or dedicated
    profile, safe credential setup, backups without losing send evidence.

## Development gates

Each step uses a branch, synchronized live version metadata, regression tests,
review, green PR CI, merge, green main CI and a versioned release. Run deterministic
integration tests in the isolated test profile. Use a full model-driven Hermes
turn only where it adds necessary evidence; database/process safety is tested
with real OS processes and mock SMTP, not judged from model prose. Never consume
cloud/Codex quota for these tests. Never access a real account or send external
mail without explicit authorization. Independent work may proceed during a slow
agent retest, but open results must remain visible and never be reported as PASS.
