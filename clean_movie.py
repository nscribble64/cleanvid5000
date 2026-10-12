
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pysubs2


# ============================================================
# SETTINGS
# ============================================================

INPUT = sys.argv[1] if len(sys.argv) > 1 else "movie.mkv"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "movie_muted.mkv"

# Normally leave these as None.
# If automatic selection fails, enter the global stream index
# reported by ffprobe for the correct English track.
ENGLISH_AUDIO_STREAM_OVERRIDE = None
ENGLISH_SUBTITLE_STREAM_OVERRIDE = None

# If False, standalone words like "God" or "Jesus" are only
# detected when the subtitle line contains ! or ?.
CENSOR_STANDALONE_RELIGIOUS_WORDS = False

# Expand each mute interval slightly to cover the word's edges.
PAD_MS = 80


# ============================================================
# PROFANITY WORDS
# ============================================================

BAD_WORDS = set("""
fuck fucking fucked fucker fuckers fuckin fuk fuking fuked fuker
fck fcking fcked fukker phuck phuk phuking fukk fukkin
shit shits shitty shitting shithead shitheads shite shyt sh1t
bitch bitches bitchy b1tch biatch beeyatch bytch betch bich biotch
ass asses asshole assholes asswhole asswholes azz azzhole azzholes
azzhat azzhats asshat asshats asswipe asswipes arse arsehole arseholes
jackass jackasses jackazz jackazzes jackaz jackarse
dick dicks dickhead dickheads dickweed dickweeds d1ck dik dyk dikk
cunt cunts cuntface cunthead cnt c0nt cuntz
bastard bastards basterd basterds
piss pissed pissing pisser pisses p1ss
crap crappy crapped crapper
bullshit bullsh1t bullcrap
motherfucker motherfuckers motherfucking motherfucked
mfer mf mofo motherfuker motherfuking motherfcker
damn dammit damnit damned damm d4mn
goddamn goddam goddammit goddamnit goddamned goddamning goddamns
goddamm g0ddamn g0ddam g0ddammit g0ddamnit god@mn god@mm
hell hells hella
slut sluts slutty whore whores whoring ho hoes
prick pricks twat twats douche douchebag douchebags douchbag
dumbass dumbasses smartass smartasses dipshit dipshits
jackshit jackshits shitbag shitbags shitface shitfaces shitforbrains
""".split())


# ============================================================
# RELIGIOUS EXCLAMATIONS
# ============================================================

RELIGIOUS_PHRASES = {
    "oh my god",
    "omg",
    "oh god",
    "dear god",
    "good god",
    "god almighty",
    "god help me",
    "god damn",
    "god damn it",
    "goddamn",
    "goddammit",
    "goddamnit",
    "for god sake",
    "for gods sake",
    "oh my goodness",
    "goodness gracious",
    "for goodness sake",
    "oh jesus",
    "oh jesus christ",
    "jesus christ",
    "jesus h christ",
    "sweet jesus",
    "dear jesus",
    "lord jesus",
    "jesus mary and joseph",
    "jesus wept",
    "oh christ",
    "christ almighty",
    "christ above",
    "for christ sake",
    "for christs sake",
    "for chrissake",
    "chrissakes",
    "christsakes",
    "oh lord",
    "good lord",
    "dear lord",
    "lord almighty",
    "lord help me",
    "holy cow",
    "holy moly",
    "holy mackerel",
    "holy smokes",
    "holy hell",
    "holy crap",
    "holy shit",
    "holy freaking",
    "holy fricking",
    "holy frick",
    "heavens above",
    "for heavens sake",
    "heavens sake",
    "gosh darn",
    "goshdarn",
    "gosh darn it",
    "gosh darnit",
    "dang it",
    "dagnabbit",
    "blimey",
    "cor blimey",
    "good grief",
    "good golly",
}

RELIGIOUS_SINGLE_WORDS = {
    "jesus", "christ", "god", "lord",
    "jeez", "jeeze", "jeezus", "jaysus", "gawd",
}


# ============================================================
# SPELLING NORMALIZATION
# ============================================================

SPELLING_REPLACEMENTS = {
    "azz": "ass",
    "shyt": "shit",
    "sh1t": "shit",
    "fuk": "fuck",
    "fukk": "fuck",
    "fck": "fuck",
    "phuck": "fuck",
    "phuk": "fuck",
    "bich": "bitch",
    "biotch": "bitch",
    "bytch": "bitch",
    "betch": "bitch",
    "dik": "dick",
    "dikk": "dick",
    "d1ck": "dick",
    "cnt": "cunt",
    "c0nt": "cunt",
    "jackaz": "jackass",
    "jackazz": "jackass",
    "motherfuker": "motherfucker",
    "motherfuking": "motherfucking",
    "motherfcker": "motherfucker",
    "bullsh1t": "bullshit",
    "p1ss": "piss",
    "d4mn": "damn",
    "b1tch": "bitch",
    "g0ddamn": "goddamn",
    "g0ddam": "goddam",
    "g0ddammit": "goddammit",
    "g0ddamnit": "goddamnit",
    "god@mn": "goddamn",
    "god@mm": "goddam",
    "gawd": "god",
    "gawdamn": "goddamn",
    "gawdammit": "goddammit",
    "j3sus": "jesus",
    "jesuz": "jesus",
    "jeezus": "jesus",
    "jaysus": "jesus",
}


def normalize_word(word):
    word = word.lower().strip()
    word = re.sub(r"^[^\w]+", "", word)
    word = re.sub(r"[^\w]+$", "", word)
    word = word.replace("'", "").replace("-", "").replace("_", "")

    substitutions = {
        "@": "a",
        "4": "a",
        "3": "e",
        "1": "i",
        "!": "i",
        "0": "o",
        "$": "s",
        "5": "s",
        "7": "t",
        "#": "",
        "*": "",
    }

    for old, new in substitutions.items():
        word = word.replace(old, new)

    for _ in range(3):
        for old, new in SPELLING_REPLACEMENTS.items():
            word = word.replace(old, new)

    return word


NORMALIZED_BAD_WORDS = {
    normalize_word(word) for word in BAD_WORDS
}

NORMALIZED_RELIGIOUS_PHRASES = {
    tuple(normalize_word(word) for word in phrase.split())
    for phrase in RELIGIOUS_PHRASES
}

NORMALIZED_RELIGIOUS_SINGLE_WORDS = {
    normalize_word(word) for word in RELIGIOUS_SINGLE_WORDS
}


# ============================================================
# FFPROBE AND STREAM SELECTION
# ============================================================

def get_streams(filename):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=index,codec_type:stream_tags=language,title",
            "-of", "json",
            filename,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return json.loads(result.stdout).get("streams", [])


def choose_english_stream(streams, override, kind):
    if override is not None:
        for stream in streams:
            if stream["index"] == override:
                return stream["index"]

        raise RuntimeError(
            f"Configured English {kind} stream {override} not found."
        )

    ranked = []

    for stream in streams:
        tags = stream.get("tags", {})
        language = tags.get("language", "").lower().strip()
        title = tags.get("title", "").lower().strip()

        score = 0

        if language in {"eng", "en", "english"}:
            score += 10

        if "english" in title:
            score += 10

        if "commentary" in title:
            score -= 20

        if "audio description" in title or "descriptive" in title:
            score -= 10

        ranked.append((score, stream["index"]))

    ranked.sort(reverse=True)

    if not ranked or ranked[0][0] < 10:
        raise RuntimeError(
            f"Could not identify English {kind} from metadata. "
            f"Set the corresponding stream override near the top."
        )

    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        raise RuntimeError(
            f"Multiple {kind} streams appear to be English. "
            f"Set the corresponding stream override near the top."
        )

    return ranked[0][1]


# ============================================================
# SUBTITLE WORD TIMING
# ============================================================

def tokenize(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace(r"\N", " ").replace(r"\n", " ")

    return re.findall(r"[A-Za-z0-9@!#$*_-]+", text)


def estimate_word_intervals(text, start_ms, end_ms):
    words = tokenize(text)

    if not words:
        return []

    total_chars = sum(len(word) for word in words)

    if total_chars == 0:
        return []

    duration = end_ms - start_ms
    current = start_ms
    result = []

    for word in words:
        word_duration = duration * len(word) / total_chars
        start = int(current)
        end = int(current + word_duration)

        result.append((start, end, word))
        current = end

    return result


# ============================================================
# DETECT WORDS AND PHRASES TO MUTE
# ============================================================

def scan_subtitles(subs):
    found = []

    for line in subs:
        raw_words = tokenize(line.text)
        normalized = [normalize_word(word) for word in raw_words]

        timings = estimate_word_intervals(
            line.text, line.start, line.end
        )

        # Individual profanity words.
        for start, end, word in timings:
            if normalize_word(word) in NORMALIZED_BAD_WORDS:
                found.append((start, end, word))

        # Multi-word religious expressions.
        for phrase in NORMALIZED_RELIGIOUS_PHRASES:
            length = len(phrase)

            for i in range(len(normalized) - length + 1):
                if tuple(normalized[i:i + length]) == phrase:
                    found.append((
                        timings[i][0],
                        timings[i + length - 1][1],
                        " ".join(raw_words[i:i + length]),
                    ))

        # Optional standalone religious words.
        emphatic = bool(re.search(r"[!?]", line.text))

        if CENSOR_STANDALONE_RELIGIOUS_WORDS or emphatic:
            for start, end, word in timings:
                if (
                    normalize_word(word)
                    in NORMALIZED_RELIGIOUS_SINGLE_WORDS
                ):
                    found.append((start, end, word))

    return found


# ============================================================
# MERGE OVERLAPPING MUTE INTERVALS
# ============================================================

def merge_intervals(intervals):
    intervals = sorted(intervals, key=lambda item: item[0])
    merged = []

    for start, end in intervals:
        start = max(0, start)
        end = max(start + 1, end)

        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    return [(start, end) for start, end in merged]


# ============================================================
# BUILD FILTER: MUTE ONLY, NO REPLACEMENT AUDIO
# ============================================================

def build_mute_filter(intervals, english_audio_stream):
    source = f"[0:{english_audio_stream}]"

    if not intervals:
        return f"{source}anull[aout]"

    # FFmpeg volume=0 during each detected interval.
    # The original audio is silent during these times.
    expression = "+".join(
        f"between(t,{start / 1000:.3f},{end / 1000:.3f})"
        for start, end in intervals
    )

    return (
        f"{source}volume=volume=0:"
        f"enable='{expression}'[aout]"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if not os.path.isfile(INPUT):
        raise RuntimeError(f"Input movie not found: {INPUT}")

    if os.path.abspath(INPUT) == os.path.abspath(OUTPUT):
        raise RuntimeError("Input and output filenames must differ.")

    streams = get_streams(INPUT)

    audio_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "audio"
    ]

    subtitle_streams = [
        stream for stream in streams
        if stream.get("codec_type") == "subtitle"
    ]

    english_audio = choose_english_stream(
        audio_streams,
        ENGLISH_AUDIO_STREAM_OVERRIDE,
        "audio",
    )

    english_subtitle = choose_english_stream(
        subtitle_streams,
        ENGLISH_SUBTITLE_STREAM_OVERRIDE,
        "subtitle",
    )

    print(f"English audio stream: {english_audio}")
    print(f"English subtitle stream: {english_subtitle}")

    temp_dir = tempfile.mkdtemp(prefix="movie_mute_")
    subtitle_file = os.path.join(temp_dir, "english_subtitles.srt")

    try:
        print("\nExtracting subtitles...")

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", INPUT,
                "-map", f"0:{english_subtitle}",
                subtitle_file,
            ],
            check=True,
        )

        subs = pysubs2.load(subtitle_file)

        print(f"Subtitle lines loaded: {len(subs)}")
        print("Scanning for words to mute...\n")

        matches = scan_subtitles(subs)

        for start, end, word in matches:
            print(
                f"{start / 1000:8.2f}s - "
                f"{end / 1000:8.2f}s : {word}"
            )

        padded = [
            (
                max(0, start - PAD_MS),
                end + PAD_MS,
            )
            for start, end, _ in matches
        ]

        intervals = merge_intervals(padded)

        print(f"\nTotal mute intervals: {len(intervals)}")

        filter_complex = build_mute_filter(
            intervals,
            english_audio,
        )

        # Map the video without re-encoding it.
        command = [
            "ffmpeg", "-y",
            "-i", INPUT,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-c:v", "copy",
        ]

        # Preserve original audio track order.
        # Only the English track is filtered; other tracks are copied.
        output_audio_number = 0

        for stream in audio_streams:
            stream_index = stream["index"]

            if stream_index == english_audio:
                command.extend([
                    "-map", "[aout]",
                    f"-c:a:{output_audio_number}", "aac",
                    f"-b:a:{output_audio_number}", "384k",
                ])
            else:
                command.extend([
                    "-map", f"0:{stream_index}",
                    f"-c:a:{output_audio_number}", "copy",
                ])

            output_audio_number += 1

        # Preserve subtitles and other subtitle tracks.
        command.extend([
            "-map", "0:s?",
            "-c:s", "copy",
            "-map_metadata", "0",
            OUTPUT,
        ])

        print("\nProcessing movie...")
        print("Video: copied without re-encoding.")
        print("English audio: muted during detected words.")
        print("Other audio tracks: copied unchanged.")
        print("Replacement audio: none.")
        print()

        subprocess.run(command, check=True)

        print(f"\nFinished! Saved as: {OUTPUT}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"\nERROR: {error}")
        sys.exit(1)
