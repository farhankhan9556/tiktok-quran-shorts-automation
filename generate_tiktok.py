import os
import re
import json
import random
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont

# ============================================================
# TikTok Quran Shorts - FREE Pexels + CC0 Wikimedia workflow
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
        "(https://github.com/farhankhan9556/tiktok-quran-shorts-automation) "
        "requests"
    ),
    "Accept": "application/json",
}

# Short surahs are used so each generated video can contain one complete surah.
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

# Pexels search terms. Each run chooses two different visual themes.
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
# Helpers
# ============================================================

def run(cmd):
    print("$", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def download(url, path, headers=None):
    r = requests.get(url, headers=headers or {}, timeout=60)
    r.raise_for_status()
    path.write_bytes(r.content)


def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()


def split_arabic_words(text):
    # Keep Arabic words while removing common punctuation.
    text = re.sub(r"[ۖۗۚۛۙۜۢ۝﴾﴿]", " ", text)
    text = text.replace("(", " ").replace(")", " ")
    return [x for x in clean_text(text).split() if x]


def group_words(words, group_size=4):
    return [words[i:i + group_size] for i in range(0, len(words), group_size)]


def ensure_dirs():
    OUTPUT.mkdir(exist_ok=True)
    for p in OUTPUT.iterdir():
        if p.is_file():
            p.unlink()


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


# ============================================================
# Quran text
# ============================================================

def get_surah_text(surah_number):
    url = f"https://api.alquran.cloud/v1/surah/{surah_number}/quran-uthmani"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()

    if data.get("status") != "OK":
        raise RuntimeError(f"Quran API failed for surah {surah_number}")

    ayahs = data["data"]["ayahs"]
    return [
        {
            "number": a["numberInSurah"],
            "text": clean_text(a["text"]),
        }
        for a in ayahs
    ]


# ============================================================
# Wikimedia Commons CC0 recitations
# ============================================================

def commons_category_files():
    """Return all file titles in the Aaqib Azeez recitation category."""
    files = []
    cmcontinue = None

    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": "Category:Recitations of the Qur'an by Aaqib Azeez",
            "cmnamespace": "6",
            "cmtype": "file",
            "cmlimit": "max",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        r = requests.get(
            WIKIMEDIA_API,
            params=params,
            headers=WIKIMEDIA_HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()

        files.extend(x["title"] for x in data["query"]["categorymembers"])

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

    r = requests.get(
        WIKIMEDIA_API,
        params=params,
        headers=WIKIMEDIA_HEADERS,
        timeout=30,
    )
    r.raise_for_status()

    pages = r.json()["query"]["pages"]
    page = next(iter(pages.values()))

    if "imageinfo" not in page:
        return None

    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})

    license_name = (
        meta.get("LicenseShortName", {}).get("value", "")
        or meta.get("License", {}).get("value", "")
    )

    return {
        "title": title,
        "url": info["url"],
        "license": clean_text(re.sub("<[^>]+>", "", license_name)),
    }


def surah_number_from_title(title):
    m = re.search(r"Chapter\s+(\d+)", title, re.I)
    return int(m.group(1)) if m else None


def discover_cc0_recitations():
    print("Discovering CC0/public-domain Qur'an recitations...")
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
        except Exception as e:
            print("Skipping Wikimedia file:", title, e)
            continue

        if not info:
            continue

        license_text = info["license"].lower()
        if "cc0" not in license_text and "public domain" not in license_text:
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

    # One recording per surah; avoid duplicate v2 recordings unless necessary.
    unique = {}
    for c in candidates:
        if c["surah"] not in unique:
            unique[c["surah"]] = c

    result = list(unique.values())

    print(f"Found {len(result)} usable CC0/public-domain short-surah recordings.")

    if len(result) < 2:
        raise RuntimeError(
            "Fewer than 2 usable CC0/public-domain short-surah recordings were found."
        )

    return result


# ============================================================
# Pexels
# ============================================================

def pexels_search(query):
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY GitHub secret is missing.")

    r = requests.get(
        "https://api.pexels.com/v1/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={
            "query": query,
            "orientation": "portrait",
            "size": "medium",
            "per_page": 15,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("videos", [])


def choose_pexels_video(query):
    videos = pexels_search(query)
    if not videos:
        raise RuntimeError(f"No Pexels portrait videos found for: {query}")

    # Prefer videos with a reasonable portrait source and duration.
    ranked = []
    for video in videos:
        duration = float(video.get("duration") or 0)
        files = video.get("video_files") or []
        portrait_files = []

        for f in files:
            w = f.get("width") or 0
            h = f.get("height") or 0
            if h >= w and h >= 1280 and f.get("link"):
                portrait_files.append(f)

        if not portrait_files:
            continue

        # Prefer ~1080p and clips at least 15 seconds.
        portrait_files.sort(
            key=lambda f: (
                abs((f.get("width") or 0) - 1080),
                -(f.get("height") or 0),
            )
        )
        chosen = portrait_files[0]

        score = (0 if duration >= 15 else 10, abs((chosen.get("width") or 0) - 1080))
        ranked.append((score, video, chosen))

    if not ranked:
        raise RuntimeError(f"No suitable portrait Pexels video found for: {query}")

    ranked.sort(key=lambda x: x[0])
    _, video, chosen = ranked[0]

    return {
        "video_id": video.get("id"),
        "page_url": video.get("url", "https://www.pexels.com/"),
        "photographer": video.get("user", {}).get("name", "Pexels contributor"),
        "download_url": chosen["link"],
    }


# ============================================================
# Arabic overlay
# ============================================================

def render_overlay(text, surah_name, verse_number, path):
    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    font = ImageFont.truetype(str(FONT), 74)
    ref_font = ImageFont.truetype(str(FONT), 34)

    # Pillow uses libraqm on the GitHub runner because we install
    # libraqm/harfbuzz/fribidi in the workflow.
    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
        direction="rtl",
        language="ar",
        stroke_width=0,
    )

    tw = bbox[2] - bbox[0]
    x = W // 2 + tw // 2
    y = int(H * 0.62)

    # Subtle shadow/glow, no dark panel.
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

    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
        direction="rtl",
        language="ar",
    )

    ref = f"{surah_name} • {verse_number}"
    rb = draw.textbbox((0, 0), ref, font=ref_font)
    rw = rb[2] - rb[0]

    draw.text(
        ((W + rw) // 2, int(H * 0.70)),
        ref,
        font=ref_font,
        fill=(235, 235, 235, 235),
        anchor="mm",
    )

    image.save(path)


def build_overlays(ayahs, total_duration, workdir):
    overlays = []
    all_groups = []

    for ayah in ayahs:
        words = split_arabic_words(ayah["text"])
        groups = group_words(words, 4)
        all_groups.append((ayah["number"], groups))

    total_groups = sum(len(groups) for _, groups in all_groups)
    if total_groups == 0:
        raise RuntimeError("No Arabic words found.")

    group_duration = total_duration / total_groups
    t = 0.0

    for verse_number, groups in all_groups:
        for group in groups:
            overlay = workdir / f"overlay_{len(overlays):03d}.png"
            render_overlay(
                " ".join(group),
                current_surah_name,
                verse_number,
                overlay,
            )
            overlays.append(
                {
                    "path": overlay,
                    "start": t,
                    "duration": group_duration,
                }
            )
            t += group_duration

    return overlays


# ============================================================
# Video creation
# ============================================================

current_surah_name = ""


def create_video(surah, recitation_path, nature_path, output_path):
    global current_surah_name
    current_surah_name = surah["name"]

    duration = ffprobe_duration(recitation_path)

    workdir = OUTPUT / f"work_{surah['number']}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    ayahs = get_surah_text(surah["number"])
    overlays = build_overlays(ayahs, duration, workdir)

    # Scale/crop the Pexels portrait video to exactly 1080x1920.
    # Loop if it is shorter than the recitation.
    base = workdir / "base.mp4"

    run(
        [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(nature_path),
            "-t", f"{duration:.3f}",
            "-vf",
            (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "setsar=1"
            ),
            "-an",
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(base),
        ]
    )

    # Create overlay inputs and enable each one during its time range.
    cmd = ["ffmpeg", "-y", "-i", str(base), "-i", str(recitation_path)]

    for item in overlays:
        cmd += ["-loop", "1", "-i", str(item["path"])]

    filter_parts = []
    last = "[0:v]"

    for idx, item in enumerate(overlays):
        input_label = f"[{idx + 2}:v]"
        output_label = f"[ov{idx}]"
        end = item["start"] + item["duration"]

        filter_parts.append(
            f"{last}{input_label}"
            f"overlay=0:0:enable='between(t,{item['start']:.3f},{end:.3f})'"
            f"{output_label}"
        )
        last = output_label

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", last,
        "-map", "1:a:0",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    run(cmd)

    shutil.rmtree(workdir, ignore_errors=True)


# ============================================================
# Metadata
# ============================================================

def write_metadata(index, surah, recitation, pexels_info):
    text = f"""TikTok Quran Short {index}

Title:
{surah['name']} — Surah {surah['number']} | Quran Recitation

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

Visual note:
Arabic Qur'an text is displayed in groups of approximately 4 words at a time.
Timing is proportional to the complete recitation, not word-level timestamped.
"""
    (OUTPUT / f"metadata_{index}.txt").write_text(text, encoding="utf-8")


# ============================================================
# Main
# ============================================================

def main():
    if not FONT.exists():
        raise RuntimeError(f"Missing font: {FONT}")

    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY is missing.")

    ensure_dirs()

    recordings = discover_cc0_recitations()
    random.shuffle(recordings)
    selected = recordings[:2]

    used_surahs = set()

    for index, recitation in enumerate(selected, start=1):
        if recitation["surah"] in used_surahs:
            continue
        used_surahs.add(recitation["surah"])

        print(
            f"\n=== Video {index}: Surah {recitation['number']} "
            f"{recitation['name']} ==="
        )

        rec_path = OUTPUT / f"recitation_{index}.mp3"
        nature_path = OUTPUT / f"nature_{index}.mp4"
        out_path = OUTPUT / f"tiktok_quran_{index}.mp4"

        print("Downloading CC0 recitation...")
        download(recitation["url"], rec_path)

        query = NATURE_QUERIES[(index - 1) % len(NATURE_QUERIES)]
        print("Searching Pexels:", query)
        pexels_info = choose_pexels_video(query)

        print("Downloading Pexels nature video...")
        download(pexels_info["download_url"], nature_path)

        print("Creating final video...")
        create_video(
            recitation,
            rec_path,
            nature_path,
            out_path,
        )

        write_metadata(index, recitation, recitation, pexels_info)

        rec_path.unlink(missing_ok=True)
        nature_path.unlink(missing_ok=True)

        print("Created:", out_path)

    print("\nDONE")
    for p in sorted(OUTPUT.glob("tiktok_quran_*.mp4")):
        print(p, f"{p.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
