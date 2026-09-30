#!/usr/bin/env bash
# Download every collection artifact from the GitHub Actions "collect" workflow into
# data/archive, skipping runs already downloaded. Then run: python -m transit refresh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data/archive"
SEEN="$ROOT/data/.pulled_runs"
mkdir -p "$DEST"; touch "$SEEN"

for run in $(gh run list --workflow collect.yml --status completed --limit 200 --json databaseId -q '.[].databaseId'); do
  grep -qx "$run" "$SEEN" && continue
  tmp="$(mktemp -d)"
  if gh run download "$run" --dir "$tmp" 2>/dev/null; then
    # artifact layout: <tmp>/vehicles-<run>/dt=YYYY-MM-DD/part-<run>.parquet
    cp -R "$tmp"/vehicles-*/. "$DEST"/
    echo "$run" >> "$SEEN"
    echo "pulled run $run"
  fi
  rm -rf "$tmp"
done
