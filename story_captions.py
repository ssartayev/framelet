"""Align story captions to narration and render short, animated groups."""
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "assets/fonts"
TIMING_MODEL = ROOT / "runtime/models/word-timing"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 360
PlayResY: 640
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Story,Bangers,54,&H00FFFFFF,&H0039D7FF,&H00100C0A,&H40100C0A,0,0,0,0,100,100,0,0,1,3.3,1.1,5,22,22,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


@lru_cache(maxsize=1)
def timing_engine():
    from mlx_audio.stt.utils import load_model

    return load_model(TIMING_MODEL)


def token(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


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


def write_captions(directory, cues, tempo=1):
    from PIL import ImageFont

    audio = timing_engine().generate(str(directory / "narration.wav"), language="en", word_timestamps=True)
    heard = [word for segment in audio.segments for word in segment.get("words", []) if token(word["word"])]
    font = ImageFont.truetype(str(FONT_DIR / "Bangers-Regular.ttf"), 54)
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
    (directory / "story.ass").write_text(HEADER + "\n".join(events) + "\n", encoding="utf-8")
