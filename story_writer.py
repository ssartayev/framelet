import json
import os
import ssl
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi
from story_validation import story_is_reasonable

MODEL = Path(__file__).resolve().parent / "runtime/models/story-writer"

PLOT_RULES = Path(__file__).with_name("STORY_RULES.md")

TWIST_PROMPT = """Replace the ending using the plot rules. The setup and openings are fixed.
Write three to five short spoken sentences that fit the remaining word budget.
Return only JSON with the key ending, an array of those sentences.
"""


def writer_provider(action, env):
    default = next((name for name in ("groq", "gemini") if env.get(f"{name.upper()}_API_KEY", "").strip()), "local")
    return env.get(f"FRAMELET_STORY_{action.upper()}_PROVIDER", default).strip().lower()


def write_with_api(provider, prompt, context, validate, key):
    user_text = json.dumps(context, ensure_ascii=False)
    tls = ssl.create_default_context(cafile=certifi.where())
    name = {"groq": "Groq", "gemini": "Gemini"}[provider]
    for attempt in range(2):
        if provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}"}
            body = {
                "model": os.environ.get("FRAMELET_GROQ_MODEL", "openai/gpt-oss-120b"),
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": user_text}],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 2600,
            }
        else:
            model = quote(os.environ.get("FRAMELET_GEMINI_MODEL", "gemini-3.8-flash"), safe="")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"x-goog-api-key": key}
            body = {
                "systemInstruction": {"parts": [{"text": prompt}]},
                "contents": [{"parts": [{"text": user_text}]}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 4096},
            }
        request = Request(url, data=json.dumps(body).encode(),
                          headers={**headers, "Content-Type": "application/json", "User-Agent": "Framelet/1.0"})
        try:
            with urlopen(request, context=tls, timeout=45) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"{name} returned HTTP {error.code}.") from None
        except TimeoutError:
            raise RuntimeError(f"{name} timed out waiting for a response.") from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise RuntimeError(f"{name} timed out connecting to the API.") from None
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise RuntimeError(f"{name} connection failed TLS certificate verification.") from None
            raise RuntimeError(f"{name} could not connect to the API.") from None
        except ValueError:
            payload = {}
        try:
            if provider == "groq":
                content = payload["choices"][0]["message"]["content"]
            else:
                parts = payload["candidates"][0]["content"]["parts"]
                content = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError):
            result = None
        if validate(result):
            return result
        if attempt == 0:
            user_text += (
                "\nTry again. Return only the requested JSON. Keep sentences short, avoid repeated "
                "phrases, and keep the complete narration within the requested length."
            )
    raise RuntimeError(f"{name} could not produce a story within the format and length limits.")


def text_list(value, minimum, maximum, word_limit):
    return isinstance(value, list) and minimum <= len(value) <= maximum and all(
        isinstance(line, str) and 2 <= len(line.split()) <= word_limit and len(line) <= 175
        for line in value
    )


def normalize(line):
    return line.casefold().strip().rstrip(".!?")


def valid_story(value):
    return (
        isinstance(value, dict) and isinstance(value.get("title"), str) and bool(value["title"].strip())
        and isinstance(value.get("hooks"), list) and len(value["hooks"]) == 3
        and all(isinstance(value.get(key), list) and value[key]
                and all(isinstance(line, str) and line.strip() for line in value[key])
                for key in ("hooks", "setup", "ending"))
    )


if __name__ == "__main__":
    action = sys.argv[1]
    data = json.load(sys.stdin)
    duration = data.get("duration", 60)
    language = data.get("language", "en")
    target = ({30: 60, 60: 125, 120: 250} if language == "ru" else {30: 68, 60: 140, 120: 280})[duration]
    style = (" Все значения пиши на разговорном русском. Используй букву ё, числа пиши словами. "
             "Не калькируй с английского. Используй короткие фразы, как в голосовом сообщении другу. "
             "Каждая фраза должна добавлять новую деталь или реакцию."
             if language == "ru" else " Write all values in natural conversational English. Spell numbers out.")
    rules = PLOT_RULES.read_text(encoding="utf-8")
    provider = writer_provider(action, os.environ)
    if provider not in ("groq", "gemini", "local"):
        sys.exit("Story writer provider must be groq, gemini, or local.")
    key = os.environ.get(f"{provider.upper()}_API_KEY", "").strip()
    if provider != "local" and not key:
        sys.exit(f"Story writer needs {provider.upper()}_API_KEY.")
    if provider == "local":
        from mlx_lm import load
        from writer import generate_json

        engine = load(os.environ.get("FRAMELET_STORY_MODEL", str(MODEL)))

    def write(prompt, context, validate, limit):
        prompt = f"{rules}\n\n{prompt}\n{style}"
        if provider != "local":
            try:
                return write_with_api(provider, prompt, context, validate, key)
            except RuntimeError as error:
                sys.exit(str(error))
        processor = None
        if action == "twist":
            from typing import Annotated

            import outlines
            from pydantic import BaseModel, Field

            line_limit = 76 if duration == 30 else 120
            line_type = Annotated[str, Field(max_length=line_limit)]

            class Ending(BaseModel):
                ending: list[line_type] = Field(min_length=3, max_length=3 if duration == 30 else 13)

            processor = outlines.Generator(outlines.from_mlxlm(*engine), Ending).logits_processor
        return generate_json(
            prompt, json.dumps(context, ensure_ascii=False), validate, limit,
            engine=engine, processor=processor, repetition_penalty=1.08,
        )

    if action == "draft":
        prompt = (
            "Write a short fictional first-person story for spoken narration. "
            "Use one incident, at most three people, and a concrete cause and consequence. "
            "Keep the user's premise. Open on a surprising concrete detail. "
            "Each sentence must move the same incident forward. End with what actually happened. "
            "Avoid moral lessons, repetition, and twists unrelated to the opening. "
            "Return only JSON with title, hooks (three distinct possible opening sentences), "
            "setup (the incident in chronological order), and ending (the payoff). "
            f"Aim for {target} words in one hook plus setup and ending. "
            "Each array item is one short sentence. Write the setup and ending first in your planning. "
            "All three hooks describe the same event that actually happens in the setup. "
            "The ending must resolve the opening mystery with a concrete reason or consequence."
        )
        if language == "ru":
            prompt = (
                "Напиши бытовую историю от первого лица по идее пользователя. "
                "Выбери одного рассказчика и сохраняй его пол и точку зрения. "
                "У каждого персонажа одна ясная роль. Все события должны относиться к одному конфликту. "
                "Покажи, что рассказчик заметил, что сделал и что узнал. "
                "Развязка прямо отвечает на вопрос, который возник в начале. "
                f"Около {target} слов: одно вступление, затем завязка и развязка. "
                "Верни только JSON с ключами title, hooks, setup, ending. "
                "Title — название, остальные поля — массивы коротких фраз. "
                "В hooks дай три формулировки одного события из завязки. "
                "В setup покажи события по порядку, в ending раскрой конкретную причину или последствие."
            )
        result = write(
            prompt, data,
            lambda value: valid_story(value) and story_is_reasonable(value, duration),
            {30: 650, 60: 1000, 120: 1700}[duration],
        )
    else:
        old_lines = {normalize(line) for line in data["setup"] + data["ending"]}

        def new_lines(lines):
            return [line for line in lines if normalize(line) not in old_lines]

        def valid_twist(value):
            return (
                isinstance(value, dict) and text_list(value.get("ending"), 3, 13, 32)
                and len(new_lines(value["ending"])) >= 3
                and story_is_reasonable({**data, "ending": new_lines(value["ending"])}, duration)
            )

        context = {"hooks": data["hooks"], "setup": data["setup"],
                   "previous ending to avoid": " ".join(data["ending"]),
                   "ending word target": max(22, target - sum(len(line.split()) for line in data["setup"]))}
        result = write(TWIST_PROMPT, context, valid_twist, 1100)
        result["ending"] = new_lines(result["ending"])
    print(json.dumps(result))
