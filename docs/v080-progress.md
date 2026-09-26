# v0.8.0 Full Scope and Verification Ledger

This is an in-progress implementation, not a completed release.
Branch: feat/multimodel-studio. Do not push without user approval.

## Required Scope

- [x] Model-aware builtin/design/clone request construction.
- [x] Legacy voices default to clone.
- [x] Basic singing command/tool and separate singing default.
- [x] SQLite generation history with backend pages capped at 10.
- [x] History section is last in the settings page.
- [x] Connection settings no longer stretch alongside the long delivery panel.
- [ ] Voice search/filter/edit/duplicate/enable and binding management UI.
- [ ] Voice/style import/export with resource validation and consent.
- [ ] Design candidates, comparisons and traceable conversion to clone.
- [ ] Four director modes, strict unchanged-body validation, cache coverage.
- [ ] Complete session inheritance, effective-value display and commands.
- [ ] Lyrics creation/versioning and song regeneration workflow.
- [ ] Task snapshots, per-segment checkpointing, retries and resend.
- [ ] Complete history coverage, retention controls and expired state.
- [ ] Basic-model streaming playback, cancellation and shared concurrency.
- [ ] Consolidated page layout instead of duplicate preview surfaces.
- [ ] Automated desktop/mobile browser tests including pagination.
- [ ] Migration backup/rollback and documentation.
- [ ] Full automated regression and archive verification.
- [ ] Authorized real-model smoke tests (no API calls without authorization).
- [ ] Downloads test archive, no automatic push.

## Latest Evidence

2026-09-20: 123 tests passed, 1 skipped, 5 subtests passed.
These tests do not prove the unchecked scope above.
Layout CSS is edited but screenshots have not yet been inspected.

Streaming progress:
- Builtin PCM iterator validates Base64, sample boundaries, empty responses and size.
- Web preview starts bounded ephemeral jobs, polls chunks and supports cancellation.
- Browser PCM scheduling and final WAV player are implemented, not browser-verified.
- Preview uses shared synthesis semaphore/client lifecycle and writes generation history.
- Unit tests verify PCM WAV format, history, cancellation and client release.
- No paid API request has been made. Real streaming behavior remains unverified.

Workflow progress:
- Lyrics editor supports manual immutable versions, explicit AI draft/rewrite,
  loading older versions and deletion. Stores at most 200 versions.
- Failed delivery jobs have an explicit confirmed retry action in Web.
- Voice deletion checks studio references and active background jobs.
- Conversion to clone now persists source_history_id.
- In-page dialogs replace native browser confirmation in the sandboxed page.
- Existing regression remains 123 passed, 1 skipped, 5 subtests passed.
- No new tests were added in this batch, as requested.
- These workflows still require browser verification; full scope remains open.

Consolidation progress:
- Removed duplicate preview panel and its blocking generation handler.
- Unified studio retains emotion selection, connection-test voice and audio controls.
- Adapted the existing preview regression to the new controls; no new test cases.
- Background segmented generations now save merged history audio separately from
  disposable delivery segments; history recording failure does not block delivery.
- Fixed missing song mode forwarding in segmented background preparation.
- Regression: 123 passed, 1 skipped, 5 subtests passed.
- Browser connection succeeded but file navigation was denied by browser policy.
  No alternate browser surface or URL workaround was attempted; visual QA is pending.

Implementation-first batch (not tested, per user request):
- Added configurable history record count/age, confirmed resend and target session.
- Added effective session display, director inheritance and `/tts会话` commands.
- Added three-way history candidate audio comparison with mutually exclusive playback.
- Added one-time builtin style presets and immutable pre-v080 voice backup.
- Added migration/rollback documentation.
- Streaming output filenames now participate in existing audio retention cleanup.
- No tests ran in this batch. Remaining implementation includes full snapshot
  isolation, candidate batch creation, complete binding management and release
  packaging. Browser and live-provider verification remain outstanding.

Further implementation (not tested):
- Design generation supports 1-3 independently queued candidates with batch cancel.
- Added global/user/session/emotion binding list, editing and clearing.
- Persisted synthesis settings snapshots exclude API credentials and use task-local
  configuration, including director settings and emotion contexts.
- Voice deletion guards only jobs referencing that voice, including failed jobs
  that may need their reference audio on retry.
- Pending: final source review, snapshot/reference lifetime edge cases, single-file
  delivery checkpoint behavior, CSS polish, unified validation and packaging.

Recovery/layout implementation (not tested):
- Single-file delivery now persists acknowledged progress in a sidecar checkpoint.
- Failed tasks expose generation/delivery stage; new snapshotted delivery failures
  and interrupted deliveries require explicit retry instead of restart auto-send.
- Removed duplicate onboarding strip and reduced header/panel spacing.
- Added responsive comparison audio and confirmation-dialog sizing.
- No regression, browser or live-provider tests ran during this implementation batch.

Final source review in progress:
- Task-local snapshots no longer inherit later session-style changes.
- Unsaved drafts no longer conflict with the saved voice selector.
- Streaming consumes the shared rate limiter without retrying partial playback.
- Updated README with multimodel, song, history and session workflows.
- Explicit user/session bindings precede emotion fallback.
- Still no tests run; unified validation starts after remaining source review.

Unified verification, 2026-09-20:
- Added persisted generation plans and successful segment reuse on retry.
- Corrected cleanup protection placement found by regression.
- Updated existing regression expectations for confirmed delivery retry, binding
  priority, compact page layout and preserved successful segments.
- Full regression: 123 passed, 1 skipped, 5 subtests passed.
- Node syntax checks passed for app.js and studio.js.
- This does not verify browser rendering, live APIs or real OneBot delivery.
- The Downloads archive is a development test build, not a completed release.

User-reported fixes:
- AstrBot v4.28.1 bridge rejects query strings in endpoint names. All four
  parameterized Web GET calls now use the SDK's separate params object.
- Removed the broad compact-theme overrides after the user reported lost visual
  styling. Original background, fonts, radii, shadows and panel effects remain;
  only the connection/delivery layout and new feature styles are added.
- These fixes are packaged separately for user verification; browser rendering
  has not been verified locally.
