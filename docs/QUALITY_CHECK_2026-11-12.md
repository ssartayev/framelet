# Framelet quality verification — 2026-11-12

## Verdict
Technical regression checks pass. Live editorial quality and provider reliability do not pass; this is not a public-launch sign-off. Changes are being published as reliability improvements, not as proof of production readiness.

## Changes verified
- Load local `.env` without shell execution; existing deployment environment wins. Secrets remain excluded from version control.
- Fall back to the other configured editorial provider on failure, unless explicitly pinned.
- Separate candidate and original story in editorial requests to prevent reversed comparisons; strengthen duration-edit continuity and Russian-language instructions.
- Include configuration loader in the container image and comparison CLI.

## Automated and media checks
- 35 unit tests passed; 16 offline audit checks passed.
- JavaScript syntax and Git whitespace checks passed.
- Real ElevenLabs synthesis succeeded in Russian (2.04 seconds) and English (4.32 seconds), including word timings.
- One real Russian 30-second video rendered and decoded completely with FFmpeg.
- Audio generation and decoding do not verify natural intonation: no listening-quality sign-off was performed.

## Live provider comparison
One run of 20 tasks: each provider received six drafts (RU/EN, 30/60/120 seconds), two twists and two rewrites.

| Provider | Structurally valid results | Failures |
| --- | --- | --- |
| Groq | 2/10 | 7 HTTP 429, 1 HTTP 400 |
| Gemini | 0/10 | 7 timeouts, 3 HTTP 503 |

The two valid outputs were English drafts. Structural success does not mean editorial success: the short draft has unclear payment motives; the longer draft links payment, an outage, a generator and a sublet without convincing causality. There is insufficient evidence to rank provider storytelling quality or select a best model per task.

## Remaining quality blockers
- A duration rewrite in the successful video introduced unnatural Russian and changed the original resolution. The editor previously accepted it.
- Clarified editorial input and stronger instructions address this failure mode, but live validation of the revised review was blocked by provider limits. Prompt changes are not a demonstrated fix.
- Groq HTTP 400 needs a sanitized error-body investigation; the cause was not established. HTTP 429 and Gemini outages prevent reliable end-to-end generation in this run.
- Re-run a bounded comparison after provider availability recovers; require human review of causal coherence, all hook variants and the ending, plus listening checks in both languages before launch.

Local ignored evidence: `runtime/story-provider-comparison.json`, `runtime/live-verification/result.json`, `runtime/live-verification/film.mp4`, `runtime/live-verification/english-check.wav`. Raw runtime artifacts and credentials are not included in the commit.
