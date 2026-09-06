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

WIDTH = 1080
HEIGHT = 1920
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
# SHORT SURAHS
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
# PEXELS SEARCHES
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

def run_command(command):
    print("$", " ".join(str(x) for x in command))

    subprocess.run(
        command,
        check=True
    )


def download_file(url, path, headers=None):

    # Wikimedia requires a proper User-Agent.
    if (
        headers is None
        and "upload.wikimedia.org" in url
    ):
        headers = WIKIMEDIA_HEADERS

    response = requests.get(
        url,
        headers=headers or {},
        timeout=120,
        allow_redirects=True
    )

    response.raise_for_status()

    if len(response.content) < 1000:
        raise RuntimeError(
            "Downloaded file is unexpectedly small: "
            f"{len(response.content)} bytes"
        )

    path.write_bytes(
        response.content
    )


def clean_text(text):
    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


def get_duration(file_path):

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return float(
        result.stdout.strip()
    )


def prepare_output():

    OUTPUT.mkdir(
        parents=True,
        exist_ok=True
    )

    for item in OUTPUT.iterdir():

        if item.is_file():
            item.unlink()

        elif item.is_dir():
            shutil.rmtree(item)


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
            f"Quran API failed for Surah {surah_number}"
        )

    return [
        {
            "number": ayah["numberInSurah"],
            "text": clean_text(
                ayah["text"]
            )
        }
        for ayah in data["data"]["ayahs"]
    ]


# ============================================================
# WIKIMEDIA
# ============================================================

def get_wikimedia_files():

    files = []

    continuation = None

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

        if continuation:
            params["cmcontinue"] = continuation

        response = requests.get(
            WIKIMEDIA_API,
            params=params,
            headers=WIKIMEDIA_HEADERS,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        for item in data["query"]["categorymembers"]:
            files.append(
                item["title"]
            )

        if "continue" not in data:
            break

        continuation = (
            data["continue"]["cmcontinue"]
        )

    return files


def get_wikimedia_file_info(title):

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
        timeout=30
    )

    response.raise_for_status()

    pages = response.json()[
        "query"
    ]["pages"]

    page = next(
        iter(pages.values())
    )

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
    )

    if not license_name:

        license_name = (
            metadata.get(
                "License",
                {}
            ).get("value", "")
        )

    license_name = re.sub(
        r"<[^>]+>",
        "",
        license_name
    )

    return {
        "url": info["url"],
        "license": clean_text(
            license_name
        )
    }


def get_surah_number(title):

    match = re.search(
        r"Chapter\s+(\d+)",
        title,
        re.IGNORECASE
    )

    if match:
        return int(
            match.group(1)
        )

    return None


def find_cc0_recitations():

    print(
        "Finding CC0/public-domain "
        "Quran recitations..."
    )

    titles = get_wikimedia_files()

    found = {}

    for title in titles:

        if "(Murattal)" not in title:
            continue

        surah_number = get_surah_number(
            title
        )

        if surah_number not in SHORT_SURAHS:
            continue

        try:

            info = get_wikimedia_file_info(
                title
            )

        except Exception as error:

            print(
                "Could not inspect:",
                title,
                error
            )

            continue

        if not info:
            continue

        license_text = (
            info["license"].lower()
        )

        if (
            "cc0" not in license_text
            and
            "public domain"
            not in license_text
        ):
            continue

        if surah_number not in found:

            found[surah_number] = {
                "surah": surah_number,
                "name": SHORT_SURAHS[
                    surah_number
                ],
                "title": title,
                "url": info["url"],
                "license": info["license"],
            }

    result = list(
        found.values()
    )

    print(
        "Found",
        len(result),
        "usable CC0/public-domain recordings."
    )

    if len(result) < 2:

        raise RuntimeError(
            "Not enough CC0/public-domain "
            "Quran recordings found."
        )

    return result


# ============================================================
# PEXELS
# ============================================================

def search_pexels(query):

    if not PEXELS_API_KEY:

        raise RuntimeError(
            "PEXELS_API_KEY is missing."
        )

    response = requests.get(
        "https://api.pexels.com/v1/videos/search",
        headers={
            "Authorization":
                PEXELS_API_KEY
        },
        params={
            "query": query,
            "orientation": "portrait",
            "size": "large",
            "per_page": 20,
        },
        timeout=30
    )

    response.raise_for_status()

    return response.json().get(
        "videos",
        []
    )


def choose_pexels_video(query):

    videos = search_pexels(
        query
    )

    if not videos:

        raise RuntimeError(
            "No Pexels videos found for: "
            + query
        )

    possible = []

    for video in videos:

        duration = float(
            video.get(
                "duration",
                0
            )
        )

        for video_file in (
            video.get(
                "video_files",
                []
            )
        ):

            width = (
                video_file.get(
                    "width"
                )
                or 0
            )

            height = (
                video_file.get(
                    "height"
                )
                or 0
            )

            link = video_file.get(
                "link"
            )

            if not link:
                continue

            if height < width:
                continue

            if height < 1280:
                continue

            quality_difference = abs(
                width - 1080
            )

            possible.append(
                (
                    0 if duration >= 15 else 10,
                    quality_difference,
                    video,
                    video_file
                )
            )

    if not possible:

        raise RuntimeError(
            "No suitable portrait Pexels "
            "video was found."
        )

    possible.sort(
        key=lambda x: (
            x[0],
            x[1]
        )
    )

    _, _, video, video_file = possible[0]

    return {
        "video_id": video.get(
            "id"
        ),
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
        "download_url":
            video_file["link"]
    }


# ============================================================
# ARABIC TEXT OVERLAY
# ============================================================

def arabic_words(text):

    text = re.sub(
        r"[ۖۗۚۛۙۜۢ۝﴾﴿]",
        " ",
        text
    )

    text = text.replace(
        "(",
        " "
    )

    text = text.replace(
        ")",
        " "
    )

    return [
        word
        for word in clean_text(
            text
        ).split()
        if word
    ]


def make_groups(words):

    return [
        words[i:i + 4]
        for i in range(
            0,
            len(words),
            4
        )
    ]


def create_overlay(
    arabic,
    surah_name,
    verse_number,
    output_file
):

    image = Image.new(
        "RGBA",
        (
            WIDTH,
            HEIGHT
        ),
        (
            0,
            0,
            0,
            0
        )
    )

    draw = ImageDraw.Draw(
        image
    )

    font = ImageFont.truetype(
        str(FONT),
        74
    )

    reference_font = (
        ImageFont.truetype(
            str(FONT),
            32
        )
    )

    # Arabic text
    draw.text(
        (
            WIDTH // 2,
            int(
                HEIGHT * 0.62
            )
        ),
        arabic,
        font=font,
        fill=(
            255,
            255,
            255,
            255
        ),
        anchor="mm",
        direction="rtl",
        language="ar",
        stroke_width=0
    )

    # Small reference
    reference = (
        f"{surah_name} • {verse_number}"
    )

    draw.text(
        (
            WIDTH // 2,
            int(
                HEIGHT * 0.70
            )
        ),
        reference,
        font=reference_font,
        fill=(
            235,
            235,
            235,
            235
        ),
        anchor="mm"
    )

    image.save(
        output_file
    )


def create_overlays(
    ayahs,
    duration,
    workdir,
    surah_name
):

    groups = []

    for ayah in ayahs:

        words = arabic_words(
            ayah["text"]
        )

        for group in make_groups(
            words
        ):

            groups.append(
                (
                    ayah["number"],
                    group
                )
            )

    if not groups:

        raise RuntimeError(
            "No Arabic words found."
        )

    time_per_group = (
        duration
        / len(groups)
    )

    overlays = []

    current_time = 0

    for index, (
        verse_number,
        words
    ) in enumerate(groups):

        overlay_file = (
            workdir
            / f"overlay_{index:03d}.png"
        )

        create_overlay(
            " ".join(words),
            surah_name,
            verse_number,
            overlay_file
        )

        overlays.append(
            {
                "file":
                    overlay_file,
                "start":
                    current_time,
                "end":
                    current_time
                    + time_per_group
            }
        )

        current_time += (
            time_per_group
        )

    return overlays


# ============================================================
# VIDEO CREATION
# ============================================================

def create_video(
    recitation,
    recitation_file,
    nature_file,
    output_file
):

    surah_number = (
        recitation["surah"]
    )

    surah_name = (
        recitation["name"]
    )

    duration = get_duration(
        recitation_file
    )

    workdir = (
        OUTPUT
        / f"work_{surah_number}"
    )

    if workdir.exists():
        shutil.rmtree(
            workdir
        )

    workdir.mkdir(
        parents=True
    )

    print(
        "Getting Quran text..."
    )

    ayahs = get_surah_text(
        surah_number
    )

    print(
        "Creating Arabic overlays..."
    )

    overlays = create_overlays(
        ayahs,
        duration,
        workdir,
        surah_name
    )

    base_video = (
        workdir
        / "base.mp4"
    )

    print(
        "Preparing nature video..."
    )

    run_command(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(nature_file),
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
            str(base_video)
        ]
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(base_video),
        "-i",
        str(recitation_file)
    ]

    for overlay in overlays:

        command.extend(
            [
                "-loop",
                "1",
                "-i",
                str(
                    overlay["file"]
                )
            ]
        )

    filter_parts = []

    previous = "[0:v]"

    for index, overlay in enumerate(
        overlays
    ):

        input_label = (
            f"[{index + 2}:v]"
        )

        output_label = (
            f"[v{index}]"
        )

        filter_parts.append(
            f"{previous}"
            f"{input_label}"
            f"overlay=0:0:"
            f"enable='between(t,"
            f"{overlay['start']:.3f},"
            f"{overlay['end']:.3f})'"
            f"{output_label}"
        )

        previous = output_label

    filter_complex = ";".join(
        filter_parts
    )

    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            previous,
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
            str(output_file)
        ]
    )

    print(
        "Rendering final TikTok video..."
    )

    run_command(
        command
    )

    shutil.rmtree(
        workdir,
        ignore_errors=True
    )


# ============================================================
# METADATA
# ============================================================

def create_metadata(
    recitation,
    pexels
):

    metadata = f"""TITLE
{recitation['name']} — Surah {recitation['surah']} | Quran Recitation

CAPTION
Listen to the Qur'an — Surah {recitation['name']}. 🤍

#Quran #QuranRecitation #Islam #Muslim #Allah #QuranTok #TikTokIslam

QURAN RECITATION
CC0/public-domain recording from Wikimedia Commons.

File:
{recitation['title']}

License:
{recitation['license']}

Source:
{recitation['url']}

NATURE VIDEO
Pexels video ID:
{pexels['video_id']}

Photographer:
{pexels['photographer']}

Pexels page:
{pexels['page_url']}

ARABIC TEXT
Quran text obtained through Al Quran Cloud using Uthmani text.

TIMING
Arabic text is displayed approximately 4 words at a time.
Timing is proportional to the complete recitation.
"""

    (
        OUTPUT
        / "metadata_1.txt"
    ).write_text(
        metadata,
        encoding="utf-8"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=========================================="
    )

    print(
        "FREE TIKTOK QURAN VIDEO GENERATOR"
    )

    print(
        "=========================================="
    )

    if not FONT.exists():

        raise RuntimeError(
            "NotoNaskhArabic-Regular.otf "
            "was not found in fonts/"
        )

    if not PEXELS_API_KEY:

        raise RuntimeError(
            "PEXELS_API_KEY GitHub Secret "
            "is missing."
        )

    prepare_output()

    # Find available CC0 recordings.
    recordings = (
        find_cc0_recitations()
    )

    if not recordings:

        raise RuntimeError(
            "No usable CC0 recordings found."
        )

    random.shuffle(
        recordings
    )

    recitation = recordings[0]

    print(
        "\nSelected Surah:",
        recitation["name"]
    )

    print(
        "Surah number:",
        recitation["surah"]
    )

    # --------------------------------------------------------
    # Download Quran recitation
    # --------------------------------------------------------

    recitation_file = (
        OUTPUT
        / "recitation.mp3"
    )

    print(
        "\nDownloading Quran recitation..."
    )

    download_file(
        recitation["url"],
        recitation_file,
        headers=WIKIMEDIA_HEADERS
    )

    print(
        "Recitation downloaded."
    )

    # --------------------------------------------------------
    # Get nature video
    # --------------------------------------------------------

    query = random.choice(
        NATURE_QUERIES
    )

    print(
        "\nPexels search:",
        query
    )

    pexels = choose_pexels_video(
        query
    )

    nature_file = (
        OUTPUT
        / "nature.mp4"
    )

    print(
        "Downloading Pexels video..."
    )

    download_file(
        pexels["download_url"],
        nature_file
    )

    print(
        "Nature video downloaded."
    )

    # --------------------------------------------------------
    # Create final video
    # --------------------------------------------------------

    output_file = (
        OUTPUT
        / "tiktok_quran_1.mp4"
    )

    create_video(
        recitation,
        recitation_file,
        nature_file,
        output_file
    )

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    create_metadata(
        recitation,
        pexels
    )

    recitation_file.unlink(
        missing_ok=True
    )

    nature_file.unlink(
        missing_ok=True
    )

    print(
        "\n=========================================="
    )

    print(
        "VIDEO CREATED SUCCESSFULLY"
    )

    print(
        "=========================================="
    )

    print(
        output_file
    )

    print(
        "Size:",
        round(
            output_file.stat().st_size
            / 1024
            / 1024,
            2
        ),
        "MB"
    )


if __name__ == "__main__":
    main()
