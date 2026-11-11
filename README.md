# Framelet

A local prototype that turns a short idea into a six-second vertical film. Type an idea or choose **Surprise me**, approve the pitch, then generate and download the film. The prototype makes three two-second shots.

## Cloud story studio

[Reddit Stories](http://127.0.0.1:8765/stories.html) writes and edits short scripts
through Groq or Gemini. ElevenLabs supplies narration and character timestamps
in one request. FFmpeg assembles the captioned video on the server. This mode
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
behind your hosting provider's access control so visitors cannot spend your API
quota. Generated files live in `runtime/renders/`. Job state resets when the
process restarts.

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
