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
import argparse

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

MUSIC_DIR = Path(config["paths"]["music_dir"]).expanduser()
ARCHIVE_DIR = Path(config["paths"]["archive_dir"]).expanduser()
DB_PATH = Path(config["paths"]["db_path"])
CSV_PATH = Path(config["paths"]["csv_path"])
PLAYLIST_URL = config["youtube"]["playlist_url"]
LAST_RUN_PATH = Path(config["paths"]["last_run_path"])
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
SCHEMA = {
    "youtube_url": "TEXT PRIMARY KEY",
    "title": "TEXT NOT NULL",
    "available": "INTEGER NOT NULL DEFAULT 0",
    "archived": "INTEGER NOT NULL DEFAULT 0",
    "deleted": "INTEGER NOT NULL DEFAULT 0",
    "date_added": "TEXT NOT NULL",
    "last_seen": "TEXT",
    "seen_days": "INTEGER NOT NULL DEFAULT 0",
    "date_archived": "TEXT",
    "date_deleted": "TEXT",
    "upload_date": "TEXT",
    "duration": "INTEGER",
    "channel": "TEXT",
}

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
        # Build CREATE TABLE statement from schema
        column_defs = [f"{name} {defi}" for name, defi in SCHEMA.items()]
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS tracks ({','.join(column_defs)})
            """
        )

        # Check if all expected columns exist and add missing ones
        cursor = conn.execute("PRAGMA table_info(tracks)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        for column_name, column_def in SCHEMA.items():
            if column_name not in existing_columns:
                # Skip PRIMARY KEY constraint for ALTER TABLE
                alter_def = column_def.replace(" PRIMARY KEY", "")
                print(f"Adding missing column: {column_name}")
                conn.execute(
                    f"ALTER TABLE tracks ADD COLUMN {column_name} {alter_def}"
                )

def insert_tracks(entries):
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        for e in entries:
            title = e.get("title")
            video_id = e.get("id")
            url = id_to_url(video_id)

            # Insert if new
            conn.execute(
                """
                INSERT OR IGNORE INTO tracks (title, youtube_url, date_added)
                VALUES (?, ?, ?)
                """, (title, url, today)
            )

            # Update last_seen and increment seen_days ONLY if day changed
            conn.execute(
                """
                UPDATE tracks
                SET
                    seen_days = seen_days + 1,
                    last_seen = ?
                WHERE youtube_url = ?
                    AND (last_seen IS NULL OR last_seen <> ?)
                """, (today, url, today)
            )

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
    """Get metadata for tracks that don't have it"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT youtube_url
            FROM tracks
            WHERE upload_date IS NULL
            """
        ).fetchall()
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
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE tracks
                SET
                    upload_date = ?,
                    duration = ?,
                    channel = ?
                WHERE youtube_url = ?
                    AND upload_date IS NULL
                """, (upload_date, duration, channel, url)
            )

def title_to_id(title):
    path = Path(title)
    match = re.search(r"\[([A-Za-z0-9_-]{11})\]$", path.stem)
    if not match:
        return
    return match.group(1)

def id_to_url(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url

def update_availability():
    ids_on_disk = set()
    for p in MUSIC_DIR.iterdir():
        id = title_to_id(p)
        if id:
            ids_on_disk.add(id)
    ids_archived = set()
    for p in ARCHIVE_DIR.iterdir():
        id = title_to_id(p)
        if id:
            ids_archived.add(id)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT youtube_url FROM tracks").fetchall()
        for (url,) in rows:
            video_id = url.split("v=")[-1].split("&")[0]
            available = int(video_id in ids_on_disk)
            archived = int(video_id in ids_archived)
            conn.execute(
                """
                UPDATE tracks SET available = ?, archived = ?
                WHERE youtube_url = ?
                """, (available, archived, url)
            )

def download_missing():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
        SELECT youtube_url FROM tracks
        WHERE available = 0 AND archived = 0 AND deleted = 0
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

def archive_track(title):
    """Takes a title, searches for the track, then moves it to archive"""
    today = date.today().isoformat()
    results = list(MUSIC_DIR.glob(f"*{title}*"))
    if not results:
        print(f"No available tracks found matching: {title}")
        return

    # prompt for confirmation before moving to archive
    for src_file in results:
        print(f"Archive {src_file}? (y/n)")
        if not input().lower() == "y":
            print(f"Skipped archiving {src_file}")
            continue
        dest_file = ARCHIVE_DIR / src_file.name
        try:
            shutil.move(str(src_file), str(dest_file))
            print(f"Archived: {src_file}")
            update_availability()
            update_csv()
        except Exception as e:
            print(f"Failed to archive {src_file}: {e}")
            continue

        # update database
        id = title_to_id(src_file)
        url = id_to_url(id)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE tracks SET archived = 1, date_archived = ?
                WHERE youtube_url = ?
                """, (today, url)
            )

def delete_track(title):
    """
    Delete a track from the database. 
    This should also mark it as deleted in the database...
    """
    today = date.today().isoformat()
    results = list(MUSIC_DIR.glob(f"*{title}*")) + list(ARCHIVE_DIR.glob(f"*{title}*"))
    if not results:
        print(f"No available tracks found matching: {title}")
        return

    # prompt for confirmation before deleting
    for result in results:
        print(f"Delete {result}? (y/n)")
        if not input().lower() == "y":
            print(f"Skipped deleting {result}")
            continue
        try:
            result.unlink()
            print(f"Deleted: {result}")
        except Exception as e:
            print(f"Failed to delete {result}: {e}")
            continue

        # update database
        id = title_to_id(result)
        url = id_to_url(id)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE tracks SET deleted = 1, date_deleted = ?
                WHERE youtube_url = ?
                """, (today, url)
            )

def main(args):
    if (title := args.archive):
        archive_track(title)
        return

    if (title := args.delete):
        delete_track(title)
        return

    print(f"🎵 Running music_warehouse at {datetime.now().isoformat()}")
    if run_today() and not args.debug:
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
    parser = argparse.ArgumentParser(description = "Sync YT Music Replay Mix")
    parser.add_argument("-a", "--archive", type = str, help = "Title of track to archive")
    parser.add_argument("-d", "--delete", type = str, help = "Title of track to delete")
    parser.add_argument("-D", "--debug", action = "store_true", help = "Debug mode: Always run")
    args = parser.parse_args()
    main(args)
