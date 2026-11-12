"""Compare the configured story models on identical English and Russian tasks."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from statistics import median
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


for language, story, instruction in (
    ("en", CASES[2][2], "Reveal why the landlord asked the neighbour to keep this secret."),
    ("ru", CASES[3][2], "Пусть сестра прямо объяснит, зачем она пришла в кафе."),
):
    draft = {"title": "A receipt" if language == "en" else "Конверт", **story}
    draft["hooks"] = (
        ["My neighbour paid my bill without asking.", "I found someone else's payment on my account.", "The receipt showed a payment I hadn't made."]
        if language == "en" else
        ["Я увидела мужа с сестрой в кафе.", "Муж и сестра сидели за одним столиком.", "На столе между ними лежал конверт с моим именем."]
    )
    CASES.append(("rewrite", language, {"story": draft, "scope": "ending", "instruction": instruction}))


def run_provider(provider, cases):
    results = []
    for action, language, data in cases:
        started = time.monotonic()
        command = [sys.executable, "story_writer.py", action]
        duration = data.get("duration", 60 if action == "draft" else 30)
        result = {"action": action, "language": language, "duration": duration,
                  "model": os.environ.get(f"FRAMELET_{provider.upper()}_MODEL", "openai/gpt-oss-120b" if provider == "groq" else "gemini-3.8-flash"),
                  "rules_sha256": hashlib.sha256((ROOT / "STORY_RULES.md").read_bytes()).hexdigest(),
                  "revision": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()}
        try:
            process = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, timeout=100,
                input=json.dumps({**data, "language": language, "duration": duration}, ensure_ascii=False),
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
    parser.add_argument("--repeats", type=int, default=1, choices=range(1, 6),
                        help="Repeat each case to measure consistency.")
    options = parser.parse_args()
    providers = (options.provider,) if options.provider else ("groq", "gemini")
    expanded = [(action, language, {**data, "duration": duration})
                for action, language, data in CASES[:2] for duration in (30, 60, 120)] + CASES[2:]
    cases = (CASES[:1] if options.quick else expanded) * options.repeats
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
    for provider, rows in results.items():
        for action in dict.fromkeys(row["action"] for row in rows):
            attempts = [row for row in rows if row["action"] == action]
            successful = [row["seconds"] for row in attempts if "output" in row]
            latency = f", median {median(successful):.2f}s" if successful else ""
            print(f"{provider} / {action}: {len(successful)}/{len(attempts)} valid{latency}")
    print(f"Results saved to {report}")
