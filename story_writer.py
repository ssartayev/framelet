import json
import random
import sys
from pathlib import Path
from typing import Annotated

import outlines
from mlx_lm import load
from pydantic import BaseModel, Field
from writer import generate_json

MODEL = Path(__file__).resolve().parent / "runtime/models/story-writer"

PLAN_PROMPT = """Plan an original fictional first-person short drama from the user's premise.
Invent one concrete incident involving at most three people. Give the narrator a legitimate
reason to draw the wrong conclusion. Plant a physical clue that fits BOTH interpretations.
The payoff should follow from the clue. It can be funny, unsettling, or moving according to the genre.
Find tension in two people wanting understandable but incompatible things. Avoid secret
identities, unexplained crimes, surprise wealth, and coincidences that solve the story.
When a premise is supplied, keep its defining people and situation.
Write six notes in JSON: incident, stakes, wrong_belief, clue, reveal, and choice. Each note
must be one brief, concrete sentence. Do not write the finished narration yet.
"""

STORY_PROMPT = """Write the supplied plot as an original, fictional Reddit-style confession in
English, as if talking to one friend. Start inside the incident. Make every line change what the
listener knows or feels. Build the misunderstanding, plant the clue, then reveal its real meaning
in the ending and show a concrete consequence of the narrator's choice. Stick to the plot.
Use specific objects, short dialogue where natural, contractions, and changes in rhythm. Write
plainspoken sentences a person would say out loud. Let each new detail alter the listener's guess.
For longer stories, give the narrator a believable false explanation before the final reveal.
For a two-minute story, include a later update with a concrete new fact that changes how the
earlier incident reads. Connect it to the same conflict and show what happened afterward.
Avoid 'little did I know', summaries, morals, filler, generic outrage, and melodramatic clickbait.
Follow the supplied word target. Short stories need one turn. Longer stories need an extra
complication and an aftermath with a concrete consequence. Keep every line short enough to narrate.
Write three DIFFERENT one-sentence hooks, each compatible with the same setup. One begins with an
object, one with a decision, one with something someone said. Keep each under 16 words and avoid
spoiling the reveal. The setup must follow every hook without repeating it.
Return only JSON: {"title":"short title","hooks":["","",""],"setup":["",""],"ending":["",""]}.
"""

TWIST_PROMPT = """Continue this fictional first-person story after its final setup sentence.
Resolve the existing clue and conflict with a concrete new motive, then show what the narrator
decides to do. The new reveal must change what happened or why, without changing anything
already established in the setup. Do not repeat the setup or rewrite the old ending. Follow the
suggested direction if it fits. Use short spoken English sentences matching the word target. Return only JSON:
{"ending":["sentence 1","sentence 2","sentence 3"]}.
"""

TWIST_DIRECTIONS = (
    "A lie hid a smaller wrongdoing and accidentally exposed a bigger betrayal.",
    "The obvious suspect was protecting someone the narrator had blamed.",
    "The evidence is real but was left out on purpose by someone the narrator trusted.",
)

class Plot(BaseModel):
    incident: str
    stakes: str
    wrong_belief: str
    clue: str
    reveal: str
    choice: str


class Ending(BaseModel):
    ending: list[str] = Field(min_length=3, max_length=13)


class Story(Ending):
    title: str
    hooks: list[str] = Field(min_length=3, max_length=3)
    setup: list[str] = Field(min_length=7, max_length=13)
    ending: list[str] = Field(min_length=4, max_length=8)


ShortLine = Annotated[str, Field(max_length=76)]


class ShortEnding(Ending):
    ending: list[ShortLine] = Field(min_length=3, max_length=3)


class ShortStory(ShortEnding):
    title: str
    hooks: list[ShortLine] = Field(min_length=3, max_length=3)
    setup: list[ShortLine] = Field(min_length=4, max_length=5)


class LongStory(Story):
    setup: list[str] = Field(min_length=13, max_length=24)
    ending: list[str] = Field(min_length=6, max_length=13)

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
        and all(isinstance(value.get(key), list) and all(isinstance(line, str) and line.strip() for line in value[key])
                for key in ("hooks", "setup", "ending"))
    )


if __name__ == "__main__":
    action = sys.argv[1]
    data = json.load(sys.stdin)
    duration = data.get("duration", 60)
    target = {30: 68, 60: 140, 120: 280}[duration]
    engine = load(str(MODEL))
    model = outlines.from_mlxlm(*engine)

    def write(prompt, context, schema, validate, limit):
        processor = outlines.Generator(model, schema).logits_processor
        return generate_json(prompt, json.dumps(context), validate, limit, engine=engine, processor=processor)

    if action == "draft":
        plan = write(PLAN_PROMPT, data, Plot,
                     lambda value: all(5 <= len(line.strip()) <= 220 for line in value.values()), 650)
        context = {"premise": data, "plot": plan, "word target for setup and ending together": target}
        if duration == 30:
            context["delivery"] = "Use only one thought per line. Aim for 7 to 10 words per line."
        schema = {30: ShortStory, 60: Story, 120: LongStory}[duration]
        result = write(STORY_PROMPT, context, schema, valid_story, 2400)
        openings = {normalize(line) for line in result["hooks"]}
        setup = [line for line in result["setup"] if normalize(line) not in openings]
        if len(setup) >= 4:
            result["setup"] = setup
    else:
        old_lines = {normalize(line) for line in data["setup"] + data["ending"]}

        def new_lines(lines):
            return [line for line in lines if normalize(line) not in old_lines]

        def valid_twist(value):
            return (
                isinstance(value, dict) and text_list(value.get("ending"), 3, 13, 32)
                and len(new_lines(value["ending"])) >= 3
            )

        context = {"setup": data["setup"], "previous ending to avoid": " ".join(data["ending"]),
                   "direction": random.choice(TWIST_DIRECTIONS),
                   "ending word target": max(22, target - sum(len(line.split()) for line in data["setup"]))}
        result = write(TWIST_PROMPT, context, ShortEnding if duration == 30 else Ending, valid_twist, 1100)
        result["ending"] = new_lines(result["ending"])
    print(json.dumps(result))
