import subprocess
import sqlite3
from datetime import datetime
import json
from pathlib import Path
import csv
import re
import tomllib

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

MUSIC_DIR = Path(config["paths"]["music_dir"]).expanduser()
DB_PATH = Path(config["paths"]["db_path"])
CSV_PATH = Path(config["paths"]["csv_path"])
PLAYLIST_URL = config["youtube"]["playlist_url"]

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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            youtube_url TEXT UNIQUE NOT NULL,
            available INTEGER NOT NULL DEFAULT 0,
            date_added TEXT NOT NULL
        )
        """)

def insert_tracks(entries):
    now = datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        for e in entries:
            title = e.get("title")
            video_id = e.get("id")
            url = f"https://www.youtube.com/watch?v={video_id}"

            conn.execute("""
            INSERT OR IGNORE INTO tracks (title, youtube_url, date_added)
            VALUES (?, ?, ?)
            """, (title, url, now))

def update_availability():
    ids_on_disk = set()
    for p in MUSIC_DIR.glob("*.mp3"):
        m = re.search(r"\[([A-Za-z0-9_-]{11})\]$", p.stem)
        if m:
            ids_on_disk.add(m.group(1))

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, youtube_url FROM tracks").fetchall()

        for track_id, url in rows:
            video_id = url.split("v=")[-1].split("&")[0]
            available = int(video_id in ids_on_disk)

            conn.execute(
                "UPDATE tracks SET available = ? WHERE id = ?",
                (available, track_id)
            )

def download_missing():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
        SELECT id, youtube_url FROM tracks
        WHERE available = 0
        """).fetchall()

    for track_id, url in rows:
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--embed-metadata",
            "-o", f"{MUSIC_DIR}/%(title)s [%(id)s].%(ext)s",
            url
        ]
        subprocess.run(cmd, check=True)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
            UPDATE tracks SET available = 1
            WHERE id = ?
            """, (track_id,))

def update_csv():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("""
            SELECT
                id,
                title,
                youtube_url,
                available,
                date_added
            FROM tracks
            ORDER BY date_added
        """)
        rows = cursor.fetchall()
        headers = [desc[0] for desc in cursor.description]

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

def main():
    print("Initialising database")
    init_db()

    print("Getting entries from replay playlist")
    entries = fetch_playlist_entries(PLAYLIST_URL)
    insert_tracks(entries)

    print("Downloading missing files")
    update_availability()
    download_missing()

    print("Writing to .csv")
    update_csv()

if __name__ == "__main__":
    main()