# v0.8.0 Upgrade and Rollback

This branch is in development. Do not treat passing unit tests as live-provider
or deployment validation.

## Before Upgrading

Stop the plugin and back up its entire data directory and AstrBot plugin
configuration. Include `voices.json`, `voice_refs`, persisted delivery jobs and
any outputs you need to retain. Keep the currently installed plugin archive.

On first load, an existing `voices.json` is copied to `voices.pre-v080.json`
without overwriting an earlier backup. Existing voice records without a `type`
remain clone voices. Existing reference paths and transport settings are not
rewritten.

The new `studio.sqlite3` stores styles, per-session settings, lyric versions and
generation history. Built-in styles are inserted once. Deleting a preset does
not cause it to be recreated on every restart.

## Behavior

- Each voice selects its own builtin, design or clone model.
- Singing uses builtin voices only and has an independent default voice.
- The original text response still follows AstrBot's response pipeline.
- Web generation is cancellable; generated speech appears in the last section,
  with at most ten history records per page.
- History retention controls remove records, not voice references. Audio
  retention remains governed by the existing output retention settings.
- Retrying a failed task may incur generation charges. A delivery timeout can
  mean the recipient received audio even though acknowledgement was lost.
- `/tts会话` manages the current session's voice, style, automatic TTS,
  probability and director overrides. `默认` removes an individual override;
  `重置` removes all overrides for that session.
- Lyric AI generation requires an explicit action. Loading or editing saved
  versions does not call the provider.

## Rollback

Stop the plugin before replacing files. Restore the previous plugin archive,
the backed-up configuration and `voices.json` together. Restore `voice_refs`
if references were changed. Keep `studio.sqlite3` separately; older releases
do not understand its new settings or history.

Do not copy current builtin/design records into an older clone-only release.
Do not resume the same pending delivery jobs simultaneously in old and new
instances. Review uncertain deliveries before retrying them.

## Validation Still Required

Finish the implementation ledger before release. Run the existing regression
suite once the complete changes are in place, then check the Web page inside
AstrBot on desktop and mobile. Real-provider generation and actual OneBot
delivery require the user's configured environment and explicit authorization.
