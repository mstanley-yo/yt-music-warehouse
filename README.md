# YouTube Music Warehouse

A small Python project that keeps a local copy of the music I listen to most on YouTube Music, so I can play it locally without relying on streaming services.

The script watches my YouTube Music Replay Mix, stores track information in a local SQLite database, and automatically downloads any tracks that aren’t already on my machine using `yt-dlp`.

## What this does

- Reads my YouTube Music Replay Mix (a personalized playlist)
- Stores track metadata in a SQLite database
- Keeps track of which songs are already downloaded
- Downloads missing tracks automatically
- Exports the track list to a CSV file

The script is safe to run repeatedly and is intended to be run on a schedule.

## Why I built this

Replay Mix and similar playlists are great for discovery, but they:
- Change frequently
- Remove tracks over time
- Require streaming access

This project treats Replay Mix as a signal for what I listen to often and builds a stable local music library that I control.

## How it works

```
```
YouTube Music Replay Mix
↓
yt-dlp (authenticated)
↓
Python script

* Insert new tracks into SQLite
* Check which files exist locally
* Download missing audio
  ↓
  Local music folder + CSV export

## Tech used

- Python
- SQLite (`sqlite3`)
- `yt-dlp`
- `ffmpeg`

## Authentication note

Replay Mix is private to your account, so authentication is required.

This is handled using browser cookies from in my case, Google Chrome (`--cookies-from-browser chrome`)

## Usage

Set your playlist URL in the script and run:

```bash
python sync_replay_mix.py
````

This will:

1. Fetch the current playlist
2. Update the database
3. Download missing tracks
4. Write the table to a CSV file

## Output

* Downloaded music files live in `~/Ambient/replay/`
* Metadata is stored in `warehouse.db`
* A CSV snapshot is written to `tracks.csv`

## Notes

* Tracks are never deleted automatically
* Replay Mix is treated as a changing input, not a permanent source
* This project is for personal use and learning

## License

MIT
