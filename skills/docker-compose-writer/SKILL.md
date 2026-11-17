---
name: docker-compose-writer
description: Write a docker compose file for an app and its services, with volumes, networks, health checks and secrets kept out of the file. Use for "write a docker-compose", "containerise my app", "compose file for".
metadata:
  title: Docker Compose file
  category: IT & security
  specialist: it
  inputs: the app and the services it needs
---

# Docker Compose file

## Steps
1. List the services (app, database, cache, proxy) and the image or build for each; pin image versions.
2. Add named volumes for data, a network, restart policies and health checks; depends_on with condition: service_healthy.
3. Keep passwords in an .env file or Docker secrets, never in the compose file.
4. Publish only the ports that must be reached from outside; bind admin ports to 127.0.0.1.
5. Check facts against the knowledge base (Docker Compose docs) when unsure.

## Output
The `compose.yaml` in a code block, then the `.env` example, then the commands to start, check and stop it, and a short note on each choice.
