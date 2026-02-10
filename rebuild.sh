#!/bin/bash
set -e

echo "🔄 Rebuilding yt-dlp worker with latest version..."

# Stop and remove existing containers
docker-compose down

# Rebuild images (no cache to ensure latest yt-dlp)
docker-compose build --no-cache

# Start services
docker-compose up -d

echo "✅ Rebuild complete! Checking yt-dlp version..."
docker-compose exec yt-dlp-worker yt-dlp --version

echo "📊 Service status:"
docker-compose ps
