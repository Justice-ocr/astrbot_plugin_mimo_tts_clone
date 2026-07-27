# MiMo TTS Clone v0.6.0 Architecture

The plugin calls the official OpenAI-compatible `mimo-v2.5-tts-voiceclone` API and exposes only WAV output. Voice selection follows temporary command choice, emotion, user, group, then global defaults. Reference audio Data URLs and the MiMo client are reused with bounded caches and explicit lifecycle cleanup.

## Delivery

- Default chat behavior is `text_and_audio + background`. Text is sent first, then a bounded queue generates one complete WAV and actively sends it to the original `unified_msg_origin`.
- Explicit `/tts` jobs have priority over automatic TTS jobs. Jobs in the same session retain submission order even with multiple workers.
- `blocking` remains available for compatibility. Pages preview, LLM tools, and public service calls remain synchronous.
- This is asynchronous chat delivery, not streaming audio; MiMo voiceclone returns complete audio.

## Synthesis

- All text segments are computed and validated before the first API call.
- Each segment is written atomically, then compatible WAV parts are merged into one final file.
- Failure removes segment and `.part` residue. Cleanup ignores in-progress files.
- The MiMo client applies timeout with SDK retries disabled, classifies API failures, validates Base64 and WAV structure, and writes atomically. A plugin-owned reliability controller supplies rate limiting, classified retries, exponential backoff, and circuit breaking.

## Safety And Operations

- Pages never returns the API key; a blank save preserves the configured secret.
- Voice upload requires explicit consent. Metadata saves atomically with a backup, and deletion is confined to `voice_refs`.
- Queue state is atomically persisted in `tts_jobs.json`. Unfinished work is recovered by age; a valid completed WAV skips synthesis and resumes at delivery.
- Queue depth, task history, cancellation/recovery/drop counts, latency, circuit state, retry metrics, platform capability, and the latest error are visible in Pages and `/tts状态`.
- Pages and administrator commands share the same task manager for list, cancel, clear-history, and cancel-all operations.
- Background delivery preflights explicit platform capability, tries Record first, and retries as File when configured. Unknown capability does not block a real send attempt.
- Successful background delivery deletes its WAV by default; retention mode delegates cleanup to the existing age/count policy.
- Persisted v0.5.x configuration migrates idempotently to schema version 2 without changing existing reply, access-control, or secret values.
- A real MiMo integration test exists behind `MIMO_TTS_LIVE_TEST=1`; it is skipped by default and never prints credentials.
- Release verification covers unit tests, full Python compilation, schema validation, JavaScript syntax, and a three-second fake-TTS ordering test.
