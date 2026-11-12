# Framelet

Framelet is a cloud-backed short-story studio with an optional experimental AI Video prototype. The story studio uses API services for all model inference.

## Cloud story studio

[Reddit Stories](http://127.0.0.1:8765/stories.html) writes and edits short scripts
through Groq or Gemini. ElevenLabs supplies narration and character timestamps
together in each synthesis request. Identical narration is cached within its project. FFmpeg assembles the captioned video on the server. This mode
runs without model weights, MLX, or a GPU.

Set `GROQ_API_KEY` or `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, and the ElevenLabs
voice IDs `FRAMELET_ELEVENLABS_VOICE_EN` and/or `FRAMELET_ELEVENLABS_VOICE_RU`
in the server environment. Choose a voice recorded in the corresponding language.

For a local check of the cloud flow:

```bash
bash setup.sh --stories-only
runtime/venv/bin/python app.py --stories-only
```

For a Linux deployment, build the included `Dockerfile` and configure the same
environment variables through the host's secret settings. The container opens
the story studio at `/` and listens on `PORT` (default `8765`). Put the service
behind a trusted HTTPS reverse proxy on a private backend network. Set
`FRAMELET_ACCESS_TOKEN` to a long random beta password and `FRAMELET_HTTPS_PROXY=1`.
The proxy must replace `X-Forwarded-Proto` with `https`, never forward an arbitrary
client value. Do not expose the backend port directly. The browser asks for Basic
Auth: any username, the configured token as password. Cookies become Secure in proxy mode.

Mount persistent storage at `/app/runtime/renders` (writable by UID 65534).
SQLite stores projects and idempotent requests there; drafts survive restarts.
Interrupted work is marked retryable rather than automatically repeating paid calls.
Run **one process/replica** for this beta: a process lock prevents two processes
sharing the same data directory. Browser sessions isolate projects; this is not yet
an account system or cross-device project sync. Keep backups of the volume.
The beta limits work to eight pending jobs globally and two per browser session.
It does not yet implement billing quotas or automatic storage retention.

With both keys configured, the writer starts with Groq and tries Gemini if the
request or output fails. Set `FRAMELET_STORY_DRAFT_PROVIDER`,
`FRAMELET_STORY_TWIST_PROVIDER`, or `FRAMELET_STORY_REWRITE_PROVIDER` to pin a task
to `groq` or `gemini`. Model IDs are configurable with `FRAMELET_GROQ_MODEL` and
`FRAMELET_GEMINI_MODEL`.

With both writer keys set, compare drafts, alternate endings, and targeted edits
in English and Russian:

```bash
runtime/venv/bin/python scripts/compare_story_writers.py --repeats 3
```

The JSON report under `runtime/` includes the generated text, errors, and elapsed
time. Read the outputs for coherence, engagement, natural phrasing, and whether
edits respected the source before assigning a provider to a task.

Choose a linked premise or paste source text, then write a draft. Each premise
also has a prepared 30-second Russian version. Apply an edit, read the revised
script, then preview the selected opening or export the full video. Edit
[STORY_RULES.md](STORY_RULES.md) to tune the writing rules.

## Experimental AI Video tab

The separate AI Video prototype uses Qwen3, Wan, and macOS narration to render
three two-second shots. To run it on Apple Silicon with macOS 26 and Python 3.11
or later:

```bash
bash setup.sh
runtime/venv/bin/python app.py
```

This setup downloads the AI Video models. The cloud story deployment serves the
story studio only.

## Editing and quality checks

Saved projects reopen after refresh. “Fix one sentence” changes exactly that text;
previous story versions are retained. Changing backgrounds/caption styles reuses
cached narration. Changing text, voice or model invalidates that audio cache.
Sentence editing currently re-synthesizes the full narration; audio splicing of a
single phrase is not implemented yet.

Cloud drafts receive a separate editorial check and at most one repair. When both
keys exist, the editor defaults to the other provider; override with
`FRAMELET_STORY_REVIEW_PROVIDER`. This is provisional routing, not a demonstrated
quality ranking. A failed review preserves the previous story. Prepared drafts and
manual edits do not incur this editorial API charge. Review is a useful filter,
not a guarantee of interesting stories.

Rendering allows one script-length correction and a second synthesis when needed.
It then refuses out-of-range narration instead of clipping words or dramatically
changing speed. This can cost an additional writer/editor/TTS pass. Inspect the
updated script after a timing correction. Per-project `usage.jsonl` records TTS
character counts and cache hits; it is not a complete billing ledger.

Optional voice catalogue, supplied as the `FRAMELET_VOICES` environment variable:

```json
{"ru":{"calm_ru":{"label":"Calm Russian narrator","voice_id":"YOUR_RU_VOICE_ID"}},"en":{"calm_en":{"label":"Calm English narrator","voice_id":"YOUR_EN_VOICE_ID"}}}
```

Existing `FRAMELET_ELEVENLABS_VOICE_RU/EN` values remain the default voices.
`FRAMELET_ELEVENLABS_MODEL` defaults to `eleven_multilingual_v2`; changing it requires
checking voice compatibility and timestamps against the provider. No new voices
or models have been listening-tested without live credentials.

## Verification

```bash
runtime/venv/bin/python -m unittest discover -s tests -v
runtime/venv/bin/python scripts/audit_platform.py --render
```

The audit uses synthetic audio, real FFmpeg and mocked cloud responses. The live
comparison command above requires exported keys in the shell running it; it tests
30/60/120-second drafts and records model, revision and plot-rules hash. It does not
score engagement or prove the editor/narrator's quality. Free API quotas are limited.
