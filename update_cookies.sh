#!/bin/sh

# Script to update cookies from a remote URL
# Usage: ./update_cookies.sh

set -e

# Use environment variables if available
URL="${COOKIES_URL}"
DEST="${COOKIES_PATH:-/data/cookies/cookies.txt}"

if [ -z "$URL" ]; then
    echo "[ERROR] COOKIES_URL is not set. Skipping cookie update."
    exit 1
fi

echo "[INFO] Updating cookies from $URL..."
echo "[INFO] Destination: $DEST"

# Ensure parent directory exists
mkdir -p "$(dirname "$DEST")"

# Download cookies
# Using curl if available, otherwise wget
if command -v curl >/dev/null 2>&1; then
    curl -sSL "$URL" -o "$DEST.tmp"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$DEST.tmp" "$URL"
else
    echo "[ERROR] Neither curl nor wget found. Cannot download cookies."
    exit 1
fi

# Basic validation (check if file is not empty)
if [ -s "$DEST.tmp" ]; then
    mv "$DEST.tmp" "$DEST"
    echo "[SUCCESS] Cookies updated successfully at $(date)"
    # Set permissions
    chmod 644 "$DEST"
else
    echo "[ERROR] Downloaded cookie file is empty. Keeping old cookies if they exist."
    rm -f "$DEST.tmp"
    exit 1
fi
