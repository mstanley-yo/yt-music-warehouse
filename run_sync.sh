#!/bin/bash

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd /Users/stanleyyo/Python/music_warehouse || exit 1

mkdir -p log
LOG_FILE="log/sync_replay_mix_$(date +%Y-%m-%d).log"

/opt/homebrew/bin/python3 sync_replay_mix.py >>"$LOG_FILE" 2>&1
