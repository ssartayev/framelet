# Framelet

A local prototype that turns a short idea into a six-second vertical film. Type an idea or choose **Surprise me**, approve the pitch, then generate and download the film. The prototype makes three two-second shots.

## Run on Apple Silicon

Use macOS 26 and Python 3.11 or later. From this folder, run:

```bash
bash setup.sh
runtime/venv/bin/python app.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The server listens on loopback only. Setup downloads roughly 5 GB of public model files. Generation runs locally and the model downloads require no API key. Allow one render at a time on this machine.

Qwen3 writes the pitch and three shots. Wan 2.1 through stable-diffusion.cpp generates draft footage at 192 × 336. macOS supplies narration, and FFmpeg burns in captions and exports a 360 × 640 MP4. Each shot uses a standalone prompt, so recurring objects can change appearance between shots.

Completed films and render logs are in `runtime/renders/`. Jobs in the page reset when the server restarts. Use a film's download button or keep its MP4 from the renders folder.

[Reddit Stories](http://127.0.0.1:8765/stories.html) uses Qwen3.5 to plan and write fictional stories, Qwen3-TTS for expressive narration, and Whisper for word timing. It previews three openings and exports a captioned short over an original arcade background. The story models add about 5.4 GB to setup.

Choose **English** or **Русский** for the script, narration and captions, then choose 30 seconds, 1 minute or 2 minutes. If you installed the project before Russian support was added, run `bash setup.sh` again to install the multilingual caption model. Actual length can vary with the narration and any new ending.

For cloud narration, set `ELEVENLABS_API_KEY` in the server environment and set
`FRAMELET_ELEVENLABS_VOICE_EN` and/or `FRAMELET_ELEVENLABS_VOICE_RU` to the ElevenLabs
voice IDs you want to use for each language. Choose a voice recorded in that language.
Restart Framelet and select **ElevenLabs** under Narrator. The service receives the story text
and generates each opening preview and the final narration. Subtitle alignment runs locally.

Set `GROQ_API_KEY` or `GEMINI_API_KEY` in the server environment to write stories
through an API. If both are set, Framelet defaults to Groq. Set
`FRAMELET_STORY_DRAFT_PROVIDER` and `FRAMELET_STORY_TWIST_PROVIDER` independently
to `groq`, `gemini`, or `local`. Without either key, Framelet defaults to the
local writer. The API model defaults are `openai/gpt-oss-120b` and
`gemini-3.8-flash`, configurable with `FRAMELET_GROQ_MODEL` and
`FRAMELET_GEMINI_MODEL`.

To compare both providers, export both keys and run
`runtime/venv/bin/python scripts/compare_story_writers.py`. The report in
`runtime/story-provider-comparison.json` records English and Russian drafts,
alternate endings, request errors, and elapsed time.

Edit [STORY_RULES.md](STORY_RULES.md) to tune plot structure, openings, and endings.
Framelet loads the rules for each draft and alternate ending.
