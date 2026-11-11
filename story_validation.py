"""Reject scripts that are too long or stuck repeating before narration."""
import re


def story_is_reasonable(story, duration):
    try:
        hooks = story["hooks"]
        body = [*story["setup"], *story["ending"]]
        if not hooks or not all(isinstance(line, str) and 0 < len(line.strip()) <= 175
                                for line in [*hooks, *body]):
            return False
        maximum = {30: 85, 60: 165, 120: 320}[duration]
        for hook in hooks:
            lines = [re.findall(r"[^\W_]+", line.casefold()) for line in [hook, *body]]
            if any(not line for line in lines) or len({tuple(line) for line in lines}) != len(lines):
                return False
            words = [word for line in lines for word in line]
            if len(words) > maximum:
                return False
            phrases = [tuple(words[i:i + 7]) for i in range(len(words) - 6)]
            if len(phrases) != len(set(phrases)):
                return False
        return True
    except (KeyError, IndexError, TypeError):
        return False
