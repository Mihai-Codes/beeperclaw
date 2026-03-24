#!/bin/bash
set -e

echo "Building BeeperClaw Docker image..."
docker build -t beeperclaw:latest .

echo "Testing Docker run..."
docker run --rm \
  -e MATRIX_HOMESERVER=https://matrix.beeper.com \
  -e MATRIX_USERNAME=test \
  -e MATRIX_PASSWORD=test \
  -e OPENCODE_SERVER_URL=http://host.docker.internal:4096 \
  beeperclaw:latest --help

echo "Docker build successful!"