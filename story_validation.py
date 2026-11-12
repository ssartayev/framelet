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
            if not {30: 30, 60: 70, 120: 150}[duration] <= len(words) <= maximum:
                return False
            phrases = [tuple(words[i:i + 7]) for i in range(len(words) - 6)]
            if len(phrases) != len(set(phrases)):
                return False
        return True
    except (KeyError, IndexError, TypeError):
        return False


def story_language_matches(story, language):
    """Catch gross wrong-language output; proper names may retain another alphabet."""
    text = " ".join([*story.get("hooks", []), *story.get("setup", []), *story.get("ending", [])])
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    cyrillic = sum("а" <= char.lower() <= "я" or char.lower() == "ё" for char in letters) / len(letters)
    return cyrillic >= .6 if language == "ru" else cyrillic <= .1
