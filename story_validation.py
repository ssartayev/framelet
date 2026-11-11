"""Reject scripts that are too long or stuck repeating before narration."""
import re


def story_is_reasonable(story, duration):
    try:
        lines = [story["hooks"][0], *story["setup"], *story["ending"]]
        if not all(isinstance(line, str) and 0 < len(line.strip()) <= 175 for line in lines):
            return False
        words = re.findall(r"[^\W_]+", " ".join(lines).casefold())
        if len(words) > {30: 85, 60: 165, 120: 320}[duration]:
            return False
        phrases = [tuple(words[i:i + 7]) for i in range(len(words) - 6)]
        return len(phrases) == len(set(phrases))
    except (KeyError, IndexError, TypeError):
        return False
