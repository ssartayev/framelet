import json
import os
import random
import sys
from pathlib import Path
from typing import Annotated

import outlines
from mlx_lm import load
from pydantic import BaseModel, Field
from writer import generate_json
from story_validation import story_is_reasonable

MODEL = Path(__file__).resolve().parent / "runtime/models/story-writer"

TWIST_PROMPT = """Continue this fictional first-person story after its final setup sentence.
Resolve the existing clue and conflict with a concrete new motive, then show what the narrator
decides to do. The new reveal must change what happened or why, without changing anything
already established in the setup. Do not repeat the setup or rewrite the old ending. Follow the
suggested direction if it fits. Use short spoken sentences matching the word target. Return only JSON:
{"ending":["sentence 1","sentence 2","sentence 3"]}.
"""

TWIST_DIRECTIONS = (
    "A lie hid a smaller wrongdoing and accidentally exposed a bigger betrayal.",
    "The obvious suspect was protecting someone the narrator had blamed.",
    "The evidence is real but was left out on purpose by someone the narrator trusted.",
)

StoryLine = Annotated[str, Field(max_length=120)]


class Ending(BaseModel):
    ending: list[StoryLine] = Field(min_length=3, max_length=13)


ShortLine = Annotated[str, Field(max_length=76)]


class ShortEnding(Ending):
    ending: list[ShortLine] = Field(min_length=3, max_length=3)


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
    engine = load(os.environ.get("FRAMELET_STORY_MODEL", str(MODEL)))

    def write(prompt, context, schema, validate, limit):
        processor = outlines.Generator(outlines.from_mlxlm(*engine), schema).logits_processor
        return generate_json(prompt, json.dumps(context, ensure_ascii=False), validate, limit, engine=engine, processor=processor)

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
            "Each array item is one short sentence. The setup must work after any hook."
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
                "В hooks дай три формулировки одного вступления, в setup — события, в ending — развязку."
            )
        result = generate_json(
            prompt + style, json.dumps(data, ensure_ascii=False),
            lambda value: valid_story(value) and story_is_reasonable(value, duration),
            max_tokens={30: 650, 60: 1000, 120: 1700}[duration],
            engine=engine, repetition_penalty=1.08,
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

        context = {"setup": data["setup"], "previous ending to avoid": " ".join(data["ending"]),
                   "direction": random.choice(TWIST_DIRECTIONS),
                   "ending word target": max(22, target - sum(len(line.split()) for line in data["setup"]))}
        result = write(TWIST_PROMPT + style, context, ShortEnding if duration == 30 else Ending, valid_twist, 1100)
        result["ending"] = new_lines(result["ending"])
    print(json.dumps(result))
