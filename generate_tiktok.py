import os
import re
import random
import shutil
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# SETTINGS
# ============================================================

W = 1080
H = 1920
FPS = 30

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
FONT = ROOT / "fonts" / "NotoNaskhArabic-Regular.otf"

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

WIKIMEDIA_HEADERS = {
    "User-Agent": (
        "TikTokQuranShortsAutomation/1.0 "
        "(https://github.com/farhankhan9556/"
        "tiktok-quran-shorts-automation) requests"
    ),
    "Accept": "application/json",
}


# ============================================================
# SHORT SURAHS WITH CC0 RECITATIONS
# ============================================================

SHORT_SURAHS = {
    103: "Al-Asr",
    104: "Al-Humazah",
    105: "Al-Fil",
    106: "Quraysh",
    107: "Al-Ma'un",
    108: "Al-Kawthar",
    109: "Al-Kafirun",
    110: "An-Nasr",
    111: "Al-Masad",
    112: "Al-Ikhlas",
    113: "Al-Falaq",
    114: "An-Nas",
}


# ============================================================
# PEXELS NATURE SEARCHES
# ============================================================

NATURE_QUERIES = [
    "cinematic ocean waves sunset",
    "misty mountain sunrise",
    "waterfall forest cinematic",
    "rain forest leaves",
    "desert sunset dunes",
    "clouds mountains cinematic",
    "green forest sunlight",
    "lake mountains sunrise",
    "night sky stars nature",
    "ocean beach slow motion",
]


# ============================================================
# BASIC FUNCTIONS
# ============================================================

def run(cmd):
    print("$", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def download(url, path, headers=None):
    response = requests.get(
        url,
        headers=headers or {},
        timeout=60
    )

    response.raise_for_status()

    path.write_bytes(response.content)


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def split_arabic_words(text):
    text = re.sub(
        r"[ۖۗۚۛۙۜۢ۝﴾﴿]",
        " ",
        text
    )

    text = text.replace("(", " ")
    text = text.replace(")", " ")

    return [
        word
        for word in clean_text(text).split()
        if word
    ]


def group_words(words, group_size=4):
    return [
        words[i:i + group_size]
        for i in range(0, len(words), group_size)
    ]


def ensure_dirs():
    OUTPUT.mkdir(exist_ok=True)

    for item in OUTPUT.iterdir():
        if item.is_file():
            item.unlink()


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return float(result.stdout.strip())


# ============================================================
# QURAN TEXT
# ============================================================

def get_surah_text(surah_number):

    url = (
        "https://api.alquran.cloud/v1/surah/"
        f"{surah_number}/quran-uthmani"
    )

    response = requests.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if data.get("status") != "OK":
        raise RuntimeError(
            f"Quran API failed for surah {surah_number}"
        )

    ayahs = data["data"]["ayahs"]

    return [
        {
            "number": ayah["numberInSurah"],
            "text": clean_text(ayah["text"]),
        }
        for ayah in ayahs
    ]


# ============================================================
# WIKIMEDIA CC0 RECITATIONS
# ============================================================

def commons_category_files():

    files = []
    cmcontinue = None

    while True:

        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle":
                "Category:Recitations of the Qur'an by Aaqib Azeez",
            "cmnamespace": "6",
            "cmtype": "file",
            "cmlimit": "max",
        }

        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        response = requests.get(
            WIKIMEDIA_API,
            params=params,
            headers=WIKIMEDIA_HEADERS,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        files.extend(
            item["title"]
            for item in data["query"]["categorymembers"]
        )

        if "continue" not in data:
            break

        cmcontinue = data["continue"]["cmcontinue"]

    return files


def commons_file_info(title):

    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
    }

    response = requests.get(
        WIKIMEDIA_API,
        params=params,
        headers=WIKIMEDIA_HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    pages = response.json()["query"]["pages"]

    page = next(iter(pages.values()))

    if "imageinfo" not in page:
        return None

    info = page["imageinfo"][0]

    metadata = info.get(
        "extmetadata",
        {}
    )

    license_name = (
        metadata.get(
            "LicenseShortName",
            {}
        ).get("value", "")
        or
        metadata.get(
            "License",
            {}
        ).get("value", "")
    )

    license_name = re.sub(
        "<[^>]+>",
        "",
        license_name
    )

    return {
        "title": title,
        "url": info["url"],
        "license": clean_text(license_name),
    }


def surah_number_from_title(title):

    match = re.search(
        r"Chapter\s+(\d+)",
        title,
        re.I
    )

    if match:
        return int(match.group(1))

    return None


def discover_cc0_recitations():

    print(
        "Discovering CC0/public-domain Qur'an recitations..."
    )

    titles = commons_category_files()

    candidates = []

    wanted = set(SHORT_SURAHS)

    for title in titles:

        if "(Murattal)" not in title:
            continue

        number = surah_number_from_title(title)

        if number not in wanted:
            continue

        try:
            info = commons_file_info(title)

        except Exception as error:
            print(
                "Skipping Wikimedia file:",
                title,
                error
            )
            continue

        if not info:
            continue

        license_text = info["license"].lower()

        if (
            "cc0" not in license_text
            and
            "public domain" not in license_text
        ):
            continue

        candidates.append(
            {
                "surah": number,
                "name": SHORT_SURAHS[number],
                "title": title,
                "url": info["url"],
                "license": info["license"],
            }
        )

    unique = {}

    for item in candidates:

        if item["surah"] not in unique:
            unique[item["surah"]] = item

    result = list(unique.values())

    print(
        "Found",
        len(result),
        "usable CC0/public-domain short-surah recordings."
    )

    if len(result) < 2:
        raise RuntimeError(
            "Fewer than 2 usable CC0/public-domain "
            "short-surah recordings were found."
        )

    return result


# ============================================================
# PEXELS
# ============================================================

def pexels_search(query):

    if not PEXELS_API_KEY:
        raise RuntimeError(
            "PEXELS_API_KEY GitHub secret is missing."
        )

    response = requests.get(
        "https://api.pexels.com/v1/videos/search",
        headers={
            "Authorization": PEXELS_API_KEY
        },
        params={
            "query": query,
            "orientation": "portrait",
            "size": "medium",
            "per_page": 15,
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json().get(
        "videos",
        []
    )


def choose_pexels_video(query):

    videos = pexels_search(query)

    if not videos:
        raise RuntimeError(
            f"No Pexels portrait videos found for: {query}"
        )

    ranked = []

    for video in videos:

        duration = float(
            video.get("duration") or 0
        )

        files = video.get(
            "video_files"
        ) or []

        portrait_files = []

        for video_file in files:

            width = video_file.get(
                "width"
            ) or 0

            height = video_file.get(
                "height"
            ) or 0

            if (
                height >= width
                and
                height >= 1280
                and
                video_file.get("link")
            ):
                portrait_files.append(
                    video_file
                )

        if not portrait_files:
            continue

        portrait_files.sort(
            key=lambda item: (
                abs(
                    (item.get("width") or 0)
                    - 1080
                ),
                -(
                    item.get("height")
                    or 0
                ),
            )
        )

        chosen = portrait_files[0]

        score = (
            0 if duration >= 15 else 10,
            abs(
                (chosen.get("width") or 0)
                - 1080
            ),
        )

        ranked.append(
            (
                score,
                video,
                chosen
            )
        )

    if not ranked:
        raise RuntimeError(
            f"No suitable portrait Pexels video found "
            f"for: {query}"
        )

    ranked.sort(
        key=lambda item: item[0]
    )

    _, video, chosen = ranked[0]

    return {
        "video_id": video.get("id"),
        "page_url": video.get(
            "url",
            "https://www.pexels.com/"
        ),
        "photographer": video.get(
            "user",
            {}
        ).get(
            "name",
            "Pexels contributor"
        ),
        "download_url": chosen["link"],
    }


# ============================================================
# ARABIC OVERLAY
# ============================================================

current_surah_name = ""


def render_overlay(
    text,
    surah_name,
    verse_number,
    path
):

    image = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(image)

    font = ImageFont.truetype(
        str(FONT),
        74
    )

    ref_font = ImageFont.truetype(
        str(FONT),
        34
    )

    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
        direction="rtl",
        language="ar",
        stroke_width=0,
    )

    text_width = (
        bbox[2] - bbox[0]
    )

    x = (
        W // 2
        + text_width // 2
    )

    y = int(
        H * 0.62
    )

    # Subtle shadow
    for dx, dy, alpha in [
        (3, 3, 140),
        (-2, 2, 90),
        (2, -2, 70),
    ]:

        draw.text(
            (x + dx, y + dy),
            text,
            font=font,
            fill=(0, 0, 0, alpha),
            anchor="mm",
            direction="rtl",
            language="ar",
        )

    # White Arabic
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
        direction="rtl",
        language="ar",
    )

    reference = (
        f"{surah_name} • {verse_number}"
    )

    ref_bbox = draw.textbbox(
        (0, 0),
        reference,
        font=ref_font
    )

    ref_width = (
        ref_bbox[2]
        - ref_bbox[0]
    )

    draw.text(
        (
            (W + ref_width) // 2,
            int(H * 0.70)
        ),
        reference,
        font=ref_font,
        fill=(235, 235, 235, 235),
        anchor="mm",
    )

    image.save(path)


def build_overlays(
    ayahs,
    total_duration,
    workdir
):

    overlays = []

    all_groups = []

    for ayah in ayahs:

        words = split_arabic_words(
            ayah["text"]
        )

        groups = group_words(
            words,
            4
        )

        all_groups.append(
            (
                ayah["number"],
                groups
            )
        )

    total_groups = sum(
        len(groups)
        for _, groups in all_groups
    )

    if total_groups == 0:
        raise RuntimeError(
            "No Arabic words found."
        )

    group_duration = (
        total_duration
        / total_groups
    )

    current_time = 0.0

    for verse_number, groups in all_groups:

        for group in groups:

            overlay = (
                workdir
                / f"overlay_{len(overlays):03d}.png"
            )

            render_overlay(
                " ".join(group),
                current_surah_name,
                verse_number,
                overlay,
            )

            overlays.append(
                {
                    "path": overlay,
                    "start": current_time,
                    "duration": group_duration,
                }
            )

            current_time += group_duration

    return overlays


# ============================================================
# CREATE VIDEO
# ============================================================

def create_video(
    surah,
    recitation_path,
    nature_path,
    output_path
):

    global current_surah_name

    current_surah_name = surah["name"]

    duration = ffprobe_duration(
        recitation_path
    )

    workdir = (
        OUTPUT
        / f"work_{surah['surah']}"
    )

    if workdir.exists():
        shutil.rmtree(workdir)

    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    ayahs = get_surah_text(
        surah["surah"]
    )

    overlays = build_overlays(
        ayahs,
        duration,
        workdir
    )

    base = (
        workdir
        / "base.mp4"
    )

    # Convert Pexels video to 1080x1920.
    run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(nature_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            (
                "scale=1080:1920:"
                "force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "setsar=1"
            ),
            "-an",
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            str(base),
        ]
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(base),
        "-i",
        str(recitation_path),
    ]

    for item in overlays:

        cmd += [
            "-loop",
            "1",
            "-i",
            str(item["path"]),
        ]

    filter_parts = []

    last = "[0:v]"

    for index, item in enumerate(overlays):

        input_label = (
            f"[{index + 2}:v]"
        )

        output_label = (
            f"[ov{index}]"
        )

        start = item["start"]

        end = (
            item["start"]
            + item["duration"]
        )

        filter_parts.append(
            f"{last}{input_label}"
            f"overlay=0:0:"
            f"enable='between(t,"
            f"{start:.3f},{end:.3f})'"
            f"{output_label}"
        )

        last = output_label

    filter_complex = ";".join(
        filter_parts
    )

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        last,
        "-map",
        "1:a:0",
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    run(cmd)

    shutil.rmtree(
        workdir,
        ignore_errors=True
    )


# ============================================================
# METADATA
# ============================================================

def write_metadata(
    index,
    surah,
    recitation,
    pexels_info
):

    text = f"""TikTok Quran Short {index}

Title:
{surah['name']} — Surah {surah['surah']} | Quran Recitation

Caption:
Listen to the Qur'an — Surah {surah['name']}. 🤍
#Quran #QuranRecitation #Islam #Muslim #Allah #QuranShorts #TikTokIslam

Recitation:
CC0/public-domain recording from Wikimedia Commons.
File: {recitation['title']}
License: {recitation['license']}
Source: {recitation['url']}

Nature video:
Pexels video ID: {pexels_info['video_id']}
Photographer: {pexels_info['photographer']}
Pexels page: {pexels_info['page_url']}

Arabic text:
Qur'an text from Al Quran Cloud / Tanzil.

Timing:
Arabic words are displayed in groups of approximately 4 words.
Timing is proportional to the complete recitation.
"""

    (
        OUTPUT
        / f"metadata_{index}.txt"
    ).write_text(
        text,
        encoding="utf-8"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not FONT.exists():
        raise RuntimeError(
            f"Missing font: {FONT}"
        )

    if not PEXELS_API_KEY:
        raise RuntimeError(
            "PEXELS_API_KEY is missing."
        )

    ensure_dirs()

    print(
        "Discovering CC0/public-domain "
        "Quran recitations..."
    )

    recordings = (
        discover_cc0_recitations()
    )

    if not recordings:
        raise RuntimeError(
            "No usable CC0/public-domain "
            "Quran recordings were found."
        )

    required = {
        "surah",
        "name",
        "title",
        "url",
        "license",
    }

    missing = (
        required
        - set(recordings[0].keys())
    )

    if missing:
        raise RuntimeError(
            "Invalid recitation data. "
            f"Missing fields: {sorted(missing)}"
        )

    random.shuffle(recordings)

    recitation = recordings[0]

    print(
        f"\n=== Today's video: "
        f"Surah {recitation['surah']} "
        f"{recitation['name']} ==="
    )

    index = 1

    rec_path = (
        OUTPUT
        / "recitation_1.mp3"
    )

    nature_path = (
        OUTPUT
        / "nature_1.mp4"
    )

    output_path = (
        OUTPUT
        / "tiktok_quran_1.mp4"
    )

    print(
        "Downloading CC0 recitation..."
    )

    download(
        recitation["url"],
        rec_path
    )

    query = random.choice(
        NATURE_QUERIES
    )

    print(
        "Searching Pexels:",
        query
    )

    pexels_info = (
        choose_pexels_video(query)
    )

    print(
        "Downloading Pexels nature video..."
    )

    download(
        pexels_info["download_url"],
        nature_path
    )

    print(
        "Creating final video..."
    )

    create_video(
        recitation,
        recitation,
        rec_path,
        output_path,
    )

    write_metadata(
        index,
        recitation,
        recitation,
        pexels_info
    )

    rec_path.unlink(
        missing_ok=True
    )

    nature_path.unlink(
        missing_ok=True
    )

    print("\nDONE")

    print(
        output_path,
        f"{output_path.stat().st_size / 1024 / 1024:.1f} MB"
    )


if __name__ == "__main__":
    main()
