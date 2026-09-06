
import base64
import os
import random
import re
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
WORK = ROOT / "work"
FONT = ROOT / "fonts" / "NotoNaskhArabic-Regular.otf"

OUT.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)

W, H = 1080, 1920
FPS = 30
VIDEO_COUNT = 2

OPENAI_MODEL = "gpt-image-2"

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
WIKIMEDIA_HEADERS = {
    "User-Agent": (
        "TikTokQuranShortsBot/1.0 "
        "(https://github.com/farhankhan9556/tiktok-quran-shorts-automation) "
        "requests"
    ),
    "Accept": "application/json",
}

# Short surahs with compact CC0 recitations in the Aaqib Azeez Commons category.
SHORT_SURAHS = {
    103: "Al-Asr",
    104: "Al-Humazah",
    105: "Al-Fil",
    106: "Quraysh",
    107: "Al-Maun",
    108: "Al-Kawthar",
    109: "Al-Kafirun",
    110: "An-Nasr",
    111: "Al-Masad",
    112: "Al-Ikhlas",
    113: "Al-Falaq",
    114: "An-Nas",
}

SURAH_CATEGORY = "Category:Recitations of the Qur'an by Aaqib Azeez"


def get_json(url, params=None, timeout=60):
    r = requests.get(url, params=params, headers=WIKIMEDIA_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def download(url, path, timeout=120):
    r = requests.get(url, headers=WIKIMEDIA_HEADERS, timeout=timeout)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path


def ffmpeg(*args, check=True):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)]
    return subprocess.run(cmd, check=check)


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def get_surah(surah_number):
    url = f"https://api.alquran.cloud/v1/surah/{surah_number}/quran-uthmani"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    data = r.json()["data"]
    return data["name"], data["englishName"], data["ayahs"]


def discover_cc0_recitations():
    found = {}

    params = {
        "action": "query",
        "format": "json",
        "list": "categorymembers",
        "cmtitle": SURAH_CATEGORY,
        "cmnamespace": 6,
        "cmlimit": "max",
    }

    data = get_json(WIKIMEDIA_API, params=params)
    pages = data.get("query", {}).get("categorymembers", [])

    for page in pages:
        title = page.get("title", "")
        if "Murattal" not in title:
            continue

        match = re.search(r"Chapter\s+(\d+)", title, re.I)
        if not match:
            continue

        number = int(match.group(1))
        if number not in SHORT_SURAHS:
            continue

        info = get_json(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "titles": title,
                "iiprop": "url|extmetadata",
            },
        )

        pages2 = info.get("query", {}).get("pages", {})
        page2 = next(iter(pages2.values()), {})
        imageinfo = page2.get("imageinfo", [])
        if not imageinfo:
            continue

        ii = imageinfo[0]
        meta = ii.get("extmetadata", {})
        license_name = (
            meta.get("LicenseShortName", {}).get("value", "")
            or meta.get("License", {}).get("value", "")
        )

        if not re.search(r"\bCC0\b|Public\s*domain", license_name, re.I):
            continue

        url = ii.get("url")
        if url:
            found[number] = {
                "title": title,
                "url": url,
                "license": license_name,
            }

    return found


def generate_background(client, english_name, number, path):
    prompts = [
        (
            f"Create a cinematic vertical 9:16 nature scene inspired by the peaceful "
            f"spiritual atmosphere of Quran Surah {english_name} (chapter {number}). "
            "No people, no faces, no animals close-up, no text, no Arabic calligraphy, "
            "no religious symbols. Moody dawn light, deep natural shadows, realistic "
            "photography, mist, subtle volumetric light, premium film look, calm and "
            "respectful, highly detailed, suitable as a background for a Quran TikTok."
        ),
        (
            f"Create a serene cinematic wilderness landscape for a Quran recitation "
            f"video, inspired by Surah {english_name} chapter {number}. "
            "Vertical 9:16 composition, dramatic clouds, mountains and soft atmospheric "
            "light, realistic photography, dark elegant mood, no people, no text, no "
            "logos, no buildings, no religious symbols, premium cinematic color and "
            "depth, large clean central/lower area for Arabic subtitles."
        ),
    ]

    prompt = random.choice(prompts)
    result = client.images.generate(
        model=OPENAI_MODEL,
        prompt=prompt,
        size="1024x1792",
        output_format="png",
    )
    b64 = result.data[0].b64_json
    path.write_bytes(base64.b64decode(b64))

    # Ensure the generated image has the expected portrait shape.
    img = Image.open(path).convert("RGB")
    img = img.resize((W, H), Image.Resampling.LANCZOS)
    img.save(path, "PNG")


def split_words(text, group_size=4):
    words = text.split()
    return [" ".join(words[i:i + group_size]) for i in range(0, len(words), group_size)]


def make_overlay(text, reference, path, font_size=76):
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    font = ImageFont.truetype(str(FONT), font_size)

    # Fit long groups into the safe width.
    while True:
        bbox = draw.textbbox(
            (0, 0),
            text,
            font=font,
            direction="rtl",
            language="ar",
            stroke_width=0,
        )
        if bbox[2] - bbox[0] <= 920 or font_size <= 46:
            break
        font_size -= 4
        font = ImageFont.truetype(str(FONT), font_size)

    center_x = W // 2
    arabic_y = 1180

    # Soft shadow/glow.
    draw.text(
        (center_x + 3, arabic_y + 5),
        text,
        font=font,
        anchor="mm",
        direction="rtl",
        language="ar",
        fill=(0, 0, 0, 180),
        stroke_width=3,
        stroke_fill=(0, 0, 0, 120),
    )
    draw.text(
        (center_x, arabic_y),
        text,
        font=font,
        anchor="mm",
        direction="rtl",
        language="ar",
        fill=(255, 255, 255, 255),
    )

    small_font = ImageFont.truetype(str(FONT), 34)
    draw.text(
        (center_x, arabic_y + 100),
        reference,
        font=small_font,
        anchor="mm",
        direction="rtl",
        language="ar",
        fill=(240, 240, 240, 225),
    )

    canvas.save(path, "PNG")


def make_audio_mix(recitation, ambience, out_audio):
    duration = ffprobe_duration(recitation)

    if ambience and ambience.exists():
        # Very quiet nature ambience underneath the recitation.
        ffmpeg(
            "-stream_loop", "-1",
            "-i", ambience,
            "-i", recitation,
            "-filter_complex",
            "[0:a]volume=0.045,atrim=0:{d}[nat];"
            "[1:a]volume=1.0[rec];"
            "[nat][rec]amix=inputs=2:duration=shortest:dropout_transition=2[a]"
            .format(d=duration),
            "-map", "[a]",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", duration,
            out_audio,
        )
    else:
        ffmpeg(
            "-i", recitation,
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", duration,
            out_audio,
        )

    return duration


def create_video(background, recitation, ayahs, surah_name, surah_number, video_path, ambience):
    audio_path = WORK / f"audio_{surah_number}.m4a"
    duration = make_audio_mix(recitation, ambience, audio_path)

    # Estimate each ayah duration from its Arabic character count.
    weights = [max(1, len(a["text"])) for a in ayahs]
    total_weight = sum(weights)

    current = 0.0
    segments = []

    for ayah in ayahs:
        ayah_duration = duration * max(1, len(ayah["text"])) / total_weight
        groups = split_words(ayah["text"], 4)
        group_weights = [max(1, len(g.replace(" ", ""))) for g in groups]
        group_total = sum(group_weights)

        group_start = current
        for index, group in enumerate(groups):
            group_duration = ayah_duration * group_weights[index] / group_total
            overlay = WORK / (
                f"overlay_{surah_number}_{ayah['numberInSurah']}_{index}.png"
            )
            make_overlay(
                group,
                f"Surah {surah_number}:{ayah['numberInSurah']}",
                overlay,
            )
            segments.append((group_start, group_duration, overlay))
            group_start += group_duration

        current += ayah_duration

    # Base video: slow cinematic zoom on the AI image.
    base = WORK / f"base_{surah_number}.mp4"
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "zoompan=z='min(zoom+0.00035,1.08)':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        f"d=1:s=1080x1920:fps={FPS},"
        "format=yuv420p"
    )

    ffmpeg(
        "-loop", "1",
        "-i", background,
        "-vf", vf,
        "-t", duration,
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        base,
    )

    # Build overlay filter graph.
    inputs = ["-i", str(base), "-i", str(audio_path)]
    filters = []
    last = "[0:v]"

    for i, (start, seg_duration, overlay) in enumerate(segments):
        inputs += ["-i", str(overlay)]
        out = f"[v{i}]"
        filters.append(
            f"{last}[{i+2}:v]overlay=0:0:enable='between(t,{start:.3f},{start+seg_duration:.3f})'{out}"
        )
        last = out

    filter_complex = ";".join(filters)

    ffmpeg(
        *inputs,
        "-filter_complex", filter_complex,
        "-map", last,
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        video_path,
    )


def get_ambience():
    # Known CC0 Commons file. If unavailable, video generation continues without it.
    title = "File:Ocean Waves on a Tropical Beach.ogg"
    try:
        data = get_json(
            WIKIMEDIA_API,
            params={
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "titles": title,
                "iiprop": "url|extmetadata",
            },
        )
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        info = page.get("imageinfo", [])
        if not info:
            return None

        ii = info[0]
        license_name = (
            ii.get("extmetadata", {})
            .get("LicenseShortName", {})
            .get("value", "")
        )
        if not re.search(r"\bCC0\b|Public\s*domain", license_name, re.I):
            return None

        path = WORK / "nature_ambience.ogg"
        return download(ii["url"], path)
    except Exception as exc:
        print(f"Nature ambience unavailable; continuing without it: {exc}")
        return None


def write_metadata(index, surah_number, surah_name):
    path = OUT / f"metadata_{index}.txt"
    title = f"Surah {surah_number} | {surah_name} | Quran Recitation"
    caption = (
        f"Surah {surah_number} — {surah_name} 🌿📖\n"
        "Listen, reflect, and remember.\n\n"
        f"#Quran #QuranRecitation #Surah{surah_number} "
        "#Islam #IslamicReminder #MuslimTikTok #QuranVerse"
    )
    path.write_text(
        f"Title: {title}\n\nCaption:\n{caption}\n\n"
        "Visual: AI-generated cinematic nature background.\n"
        "Recitation: verified CC0/public-domain Wikimedia Commons recording.\n"
        "Arabic text: Tanzil/Quran text source via Al Quran Cloud.\n",
        encoding="utf-8",
    )


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY GitHub Secret is missing.")

    if not FONT.exists():
        raise RuntimeError(f"Arabic font missing: {FONT}")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print("Discovering CC0/public-domain Qur'an recitations...")
    recordings = discover_cc0_recitations()

    available = [n for n in SHORT_SURAHS if n in recordings]
    if len(available) < VIDEO_COUNT:
        raise RuntimeError(
            f"Only {len(available)} usable CC0 short-surah recordings found; "
            f"need at least {VIDEO_COUNT}."
        )

    # Shuffle so consecutive days are not always identical.
    random.shuffle(available)
    selected = available[:VIDEO_COUNT]

    ambience = get_ambience()

    for index, surah_number in enumerate(selected, start=1):
        print(f"Creating video {index}: Surah {surah_number}...")

        name_ar, english_name, ayahs = get_surah(surah_number)
        recitation = WORK / f"recitation_{surah_number}.mp3"
        download(recordings[surah_number]["url"], recitation)

        background = WORK / f"background_{surah_number}.png"
        generate_background(client, english_name, surah_number, background)

        video = OUT / f"tiktok_quran_{index}.mp4"
        create_video(
            background,
            recitation,
            ayahs,
            english_name,
            surah_number,
            video,
            ambience,
        )

        write_metadata(index, surah_number, english_name)
        print(f"Created: {video}")

    print("All TikTok videos created successfully.")


if __name__ == "__main__":
    main()
