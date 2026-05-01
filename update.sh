#!/bin/bash
# Roundstone Crossing Update & Deploy Script
# Run this on your server to deploy or update
#
# First time: ./update.sh
# Updates:    ./update.sh

set -e

IMAGE="ghcr.io/benslaughter/roundstone-crossing:latest"
DIR="$HOME/roundstone-crossing"

echo "=== Roundstone Crossing Deploy ==="

# Create directory if needed
mkdir -p "$DIR"
cd "$DIR"

# Check for .env
if [ ! -f .env ]; then
    echo ""
    echo "⚠️  No .env file found. Creating template..."
    cat > .env << 'ENV'
# Network Rail Open Data credentials
NROD_USERNAME=your_email@example.com
NROD_PASSWORD=your_nrod_password

# Realtime Trains API
RTT_TOKEN=your_rtt_token
ENV
    echo "Edit $DIR/.env with your credentials, then re-run this script."
    exit 1
fi

# Create docker-compose.yml
cat > docker-compose.yml << 'COMPOSE'
services:
  roundstone-crossing:
    image: ghcr.io/benslaughter/roundstone-crossing:latest
    container_name: roundstone-crossing
    restart: unless-stopped
    ports:
      - "8590:8590"
    volumes:
      - crossing-data:/app/data
      - crossing-logs:/app/logs
    env_file:
      - .env
    environment:
      - PYTHONUNBUFFERED=1
      - API_HOST=0.0.0.0

volumes:
  crossing-data:
  crossing-logs:
COMPOSE

echo "Pulling latest image..."
docker pull "$IMAGE"

echo "Stopping current container..."
docker compose down 2>/dev/null || true

echo "Starting new container..."
docker compose up -d

echo "Cleaning old images..."
docker image prune -f

echo ""
echo "=== Roundstone Crossing deployed! ==="
echo "Access at http://localhost:8590"
echo ""
echo "Useful commands:"
echo "  docker compose logs -f        # View logs"
echo "  docker compose restart         # Restart"
echo "  docker compose down            # Stop"
