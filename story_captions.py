"""Align story captions to narration and render short, animated groups."""
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "assets/fonts"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 360
PlayResY: 640
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Story,{font_name},54,&H00FFFFFF,&H0039D7FF,&H00100C0A,&H40100C0A,{bold},0,0,0,100,100,0,0,1,3.3,1.1,5,22,22,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def alignment_words(alignment, duration):
    """Convert ElevenLabs character offsets to word starts."""
    characters = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    if not characters or len(characters) != len(starts):
        raise ValueError("Missing character timing")
    words = []
    pending = []
    for char, start in zip(characters, starts):
        if not isinstance(char, str) or len(char) != 1 or not math.isfinite(start) or not 0 <= start <= duration + 0.1:
            raise ValueError("Invalid character timing")
        if char.isspace():
            if pending:
                words.append({"word": "".join(pending), "start": word_start})
                pending = []
        else:
            if not pending:
                word_start = start
            pending.append(char)
    if pending:
        words.append({"word": "".join(pending), "start": word_start})
    if not words or any(first["start"] > second["start"] for first, second in zip(words, words[1:])):
        raise ValueError("Invalid word timing")
    return [word for word in words if token(word["word"])]


def token(text):
    return re.sub(r"[\W_]", "", text.casefold().replace("ё", "е"))


def timestamp(seconds):
    whole, fraction = divmod(round(seconds * 100), 100)
    minutes, whole = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{whole:02}.{fraction:02}"


def word_timings(words, start, end, heard):
    """Keep the script text, using recognized words only as timing anchors."""
    anchors = {0: start, len(words): end}
    nearby = [item for item in heard if start - 0.06 <= item["start"] < end]
    matcher = SequenceMatcher(None, [token(word) for word in words],
                              [token(item["word"]) for item in nearby], autojunk=False)
    for expected, actual, count in matcher.get_matching_blocks():
        for offset in range(count):
            index = expected + offset
            anchors[index] = min(end, max(start, nearby[actual + offset]["start"]))
    last = 0
    weight = [max(2, len(token(word))) for word in words]
    boundaries = [start] * (len(words) + 1)
    boundaries[0] = anchors[0]
    for next_index in sorted(anchors)[1:]:
        elapsed = max(0, anchors[next_index] - boundaries[last])
        total = sum(weight[last:next_index])
        for index in range(last, next_index):
            boundaries[index + 1] = boundaries[index] + elapsed * weight[index] / total
        last = next_index
    return boundaries


def caption_groups(words, font):
    group = []
    for word in words:
        if group and (len(group) == 3 or font.getlength(" ".join([*group, word])) > 305):
            yield group
            group = []
        group.append(word)
        if word.endswith((",", ".", "!", "?", ";")):
            yield group
            group = []
    if group:
        yield group


def write_captions(directory, cues, heard, tempo=1, language="en"):
    from PIL import ImageFont

    font_path = FONT_DIR / "Bangers-Regular.ttf"
    font_name = "Bangers"
    if language == "ru":
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        font_name = "DejaVu Sans"
        if not font_path.is_file():
            font_path = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
            font_name = "Arial"
    font = ImageFont.truetype(str(font_path), 54)
    events = []
    for start, end, line in cues:
        words = line.upper().replace("{", "").replace("}", "").replace("\\", "").split()
        if not words:
            continue
        boundaries = word_timings(words, start, end, heard)
        cursor = 0
        for group in caption_groups(words, font):
            size = min(54, int(54 * 305 / font.getlength(" ".join(group))))
            for index in range(len(group)):
                begin, finish = boundaries[cursor + index:cursor + index + 2]
                if finish - begin < 0.015:
                    continue
                prefix = rf"{{\an5\pos(180,363)\fs{size}}}"
                if index == 0:
                    prefix = rf"{{\an5\pos(180,363)\fs{size}\fscx94\fscy94\t(0,95,\fscx100\fscy100)}}"
                colored = [
                    rf"{{\c&H39D7FF&}}{word}{{\c&HFFFFFF&}}" if j == index else word
                    for j, word in enumerate(group)
                ]
                events.append(f"Dialogue: 0,{timestamp(begin / tempo)},{timestamp(finish / tempo)},Story,,0,0,0,,"
                              + prefix + " ".join(colored))
            cursor += len(group)
    header = HEADER.format(font_name=font_name,
                           bold=-1 if language == "ru" else 0)
    (directory / "story.ass").write_text(header + "\n".join(events) + "\n", encoding="utf-8")
