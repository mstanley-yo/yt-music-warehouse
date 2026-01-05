import subprocess
import sqlite3
from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import shutil
import csv
import re
import tomllib

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

MUSIC_DIR = Path(config["paths"]["music_dir"]).expanduser()
DB_PATH = Path(config["paths"]["db_path"])
CSV_PATH = Path(config["paths"]["csv_path"])
PLAYLIST_URL = config["youtube"]["playlist_url"]
LAST_RUN_PATH = Path(config["paths"]["last_run_path"])

def run_today():
    if LAST_RUN_PATH.exists():
        last_run = LAST_RUN_PATH.read_text().strip()
        return last_run == date.today().isoformat()
    return False

def mark_run_complete():
    with open(LAST_RUN_PATH, "w") as p:
        p.write(date.today().isoformat())

def fetch_playlist_entries(PLAYLIST_URL):
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", "chrome",
        "--dump-single-json",
        "--flat-playlist",
        PLAYLIST_URL
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    return data["entries"]

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            youtube_url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            available INTEGER NOT NULL DEFAULT 0,

            -- Bookkeeping 
            date_added TEXT NOT NULL,
            last_seen TEXT,
            seen_days INTEGER NOT NULL DEFAULT 0,

            -- Metadata
            upload_date TEXT,
            duration INTEGER,
            channel TEXT
        )
        """)

def insert_tracks(entries):
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        for e in entries:
            title = e.get("title")
            video_id = e.get("id")
            url = f"https://www.youtube.com/watch?v={video_id}"

            # Insert if new
            conn.execute("""
            INSERT OR IGNORE INTO tracks (title, youtube_url, date_added)
            VALUES (?, ?, ?)
            """, (title, url, today))

            # Update last_seen and increment seen_days ONLY if day changed
            conn.execute("""
            UPDATE tracks
            SET
                seen_days = seen_days + 1,
                last_seen = ?
            WHERE youtube_url = ?
                AND (last_seen IS NULL OR last_seen <> ?)
            """, (today, url, today))

def fetch_video_metadata(url):
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", "chrome",
        "--dump-json",
        url
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True)
    return json.loads(result.stdout)

def update_metadata():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT youtube_url
            FROM tracks
            WHERE upload_date IS NULL
        """).fetchall()

    for (url,) in rows:
        try:
            meta = fetch_video_metadata(url)
        except Exception as e:
            print(f"Metadata fetch failed: {url} ({e})")
            continue

        upload_date = meta.get("upload_date")
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        duration = meta.get("duration")
        channel = meta.get("channel")
        print(upload_date, duration, channel)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                UPDATE tracks
                SET
                    upload_date = ?,
                    duration = ?,
                    channel = ?
                WHERE youtube_url = ?
                    AND upload_date IS NULL
                """, (upload_date, duration, channel, url)
            )

def update_availability():
    ids_on_disk = set()
    for p in MUSIC_DIR.iterdir():
        m = re.search(r"\[([A-Za-z0-9_-]{11})\]$", p.stem)
        if m:
            ids_on_disk.add(m.group(1))

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT youtube_url FROM tracks").fetchall()

        for (url,) in rows:
            video_id = url.split("v=")[-1].split("&")[0]
            available = int(video_id in ids_on_disk)

            conn.execute(
                "UPDATE tracks SET available = ? WHERE youtube_url = ?",
                (available, url)
            )

def download_missing():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
        SELECT youtube_url FROM tracks
        WHERE available = 0
        """).fetchall()

    for (url,) in rows:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            cmd = [
                "yt-dlp",
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "--audio-format", "m4a",
                "--postprocessor-args", 
                "ffmpeg:-c:a aac -b:a 256k",
                "--embed-metadata",
                "--embed-thumbnail",
                "-o", str(tmpdir / "%(title)s [%(id)s].%(ext)s"),
                url
            ]

            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Download failed: {url} ({e})")
                continue

            # Move successful outputs to MUSIC_DIR
            for file in tmpdir.iterdir():
                shutil.move(str(file), MUSIC_DIR)

            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                UPDATE tracks SET available = 1
                WHERE youtube_url = ?
                """, (url,))

def update_csv():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("""
            SELECT *
            FROM tracks
            ORDER BY last_seen
        """)
        rows = cursor.fetchall()
        headers = [desc[0] for desc in cursor.description]
    
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

def main():
    print(f"🎵 Running music_warehouse at {datetime.now().isoformat()}")
    if run_today():
       print(f"✅ music_warehouse already ran today; skipping.\n")
       return 

    print("🗄️ Initialising database")
    init_db()

    print("👀 Getting entries from replay playlist")
    entries = fetch_playlist_entries(PLAYLIST_URL)
    insert_tracks(entries)

    print("🏷️ Updating metadata")
    update_metadata()

    print("📥 Downloading missing files")
    update_availability()
    download_missing()

    print("📊 Writing to .csv")
    update_csv()

    mark_run_complete()
    print("✅ music_warehouse run completed successfully\n")

if __name__ == "__main__":
    main()
