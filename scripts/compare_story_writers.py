"""Compare the configured story models on identical English and Russian tasks."""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("draft", "en", {"idea": "My neighbour paid my electricity bill, then left a note asking me not to tell my landlord."}),
    ("draft", "ru", {"idea": "Муж скрывал от жены тайные встречи с её сестрой."}),
    ("twist", "en", {
        "hooks": ["My neighbour paid my bill without asking."],
        "setup": ["I found a receipt under my door.", "My neighbour asked me to keep it from our landlord."],
        "ending": ["She had accidentally opened my mail.", "She paid it to make amends."],
    }),
    ("twist", "ru", {
        "hooks": ["Муж три недели встречался с моей сестрой по пятницам."],
        "setup": ["Я увидела их за одним столиком в кафе.", "На столе лежал конверт с моим именем."],
        "ending": ["Они собирали деньги на мой подарок.", "В конверте лежали билеты на концерт."],
    }),
]


def run_provider(provider, cases):
    results = []
    for action, language, data in cases:
        started = time.monotonic()
        command = [sys.executable, "story_writer.py", action]
        result = {"action": action, "language": language}
        try:
            process = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, timeout=100,
                input=json.dumps({**data, "language": language, "duration": 60}, ensure_ascii=False),
                env={**os.environ, f"FRAMELET_STORY_{action.upper()}_PROVIDER": provider},
            )
            if process.returncode == 0:
                result["output"] = json.loads(process.stdout)
            else:
                detail = process.stderr.strip()
                result["error"] = detail if detail.startswith(("Groq ", "Gemini ")) and "\n" not in detail else "Writer failed."
        except (subprocess.TimeoutExpired, ValueError):
            result["error"] = "Writer timed out or returned an unreadable response."
        result["seconds"] = round(time.monotonic() - started, 2)
        results.append(result)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Run one draft per provider to check connectivity.")
    parser.add_argument("--provider", choices=("groq", "gemini"), help="Run just one provider.")
    options = parser.parse_args()
    providers = (options.provider,) if options.provider else ("groq", "gemini")
    cases = CASES[:1] if options.quick else CASES
    keys = [f"{provider.upper()}_API_KEY" for provider in providers]
    missing = [name for name in keys if not os.environ.get(name, "").strip()]
    if missing:
        sys.exit("Missing exported variables: " + ", ".join(missing))
    print(f"Testing {', '.join(providers)}. This may take a minute.", flush=True)
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        results = dict(zip(providers, pool.map(lambda provider: run_provider(provider, cases), providers)))
    mode = "quick-check" if options.quick else "comparison"
    suffix = f"-{options.provider}" if options.provider else ""
    filename = f"story-provider-{mode}{suffix}.json"
    report = ROOT / "runtime" / filename
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Results saved to {report}")
