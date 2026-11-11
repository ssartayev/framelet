import json
import re
import sys
from pathlib import Path

from mlx_lm import generate, load
from mlx_lm.sample_utils import make_logits_processors, make_sampler

MODEL = Path(__file__).resolve().parent / "runtime/models/writer"
SYSTEM = """You are a writer for short vertical animated films. Return only valid JSON:
{"title":"3-6 words","pitch":"one sentence","shots":[{"visual":"what moves on screen","voice":"5 or fewer words"}]}
Write exactly three shots in chronological order. In shot 1, show the situation before the outcome.
In shot 2, show what changes. In shot 3, show the result. Keep the result out of earlier shots.
Keep the user's premise, or invent a fictional story if asked for a surprise. Write in English.
Describe one moving action per visual. Every visual must name the subject and repeat its visible
features so it can be generated on its own. Use a stylized animated look with no on-screen text.
Voice lines are short narration that tells the story in order. Avoid dialogue and sound effects.
"""


def make_plan(idea):
    return generate_json(SYSTEM, idea, valid_plan, max_tokens=650)


def generate_json(system, idea, validate, max_tokens, engine=None, processor=None, repetition_penalty=None):
    model, tokenizer = engine or load(str(MODEL))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": idea}]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    for temperature in (0.6, 0.3):
        if processor is not None:
            processor.reset()
        processors = make_logits_processors(repetition_penalty=repetition_penalty, repetition_context_size=96)
        if processor is not None:
            processors.append(processor)
        reply = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                         logits_processors=processors,
                         sampler=make_sampler(temp=temperature, top_p=0.9), verbose=False)
        for match in re.finditer(r"\{", reply):
            try:
                plan, _ = json.JSONDecoder().raw_decode(reply[match.start():])
                if validate(plan):
                    return plan
            except ValueError:
                pass
    raise RuntimeError("The local writer could not form a storyboard. Try a shorter idea.")


def valid_plan(plan):
    def valid_text(value, maximum):
        return isinstance(value, str) and 0 < len(value.strip()) <= maximum

    if not isinstance(plan, dict) or not valid_text(plan.get("title"), 100):
        return False
    if not valid_text(plan.get("pitch"), 360):
        return False
    shots = plan.get("shots")
    return isinstance(shots, list) and len(shots) == 3 and all(
        isinstance(shot, dict)
        and valid_text(shot.get("visual"), 800)
        and valid_text(shot.get("voice"), 100)
        and len(shot["voice"].split()) <= 8
        for shot in shots
    )


if __name__ == "__main__":
    print(json.dumps(make_plan(sys.argv[1])))
