#!/usr/bin/env bash
set -euo pipefail

# Update the complete Django group from one already-built local artifact.
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: bash deploy/update-app.sh IMAGE [COMPOSE_FILE]" >&2
  exit 2
fi
image_ref=$1
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
compose_file=${2:-"$repo_root/docker-compose.yml"}
services=(web manage worker billing_worker source_monitor)
image_id=$(docker image inspect --format '{{.Id}}' "$image_ref")

# Pin by immutable image ID, even if the supplied tag changes during deployment.
export CONFRONTA_APP_IMAGE="$image_id"
# Code-only deployment: never run migrations or bootstrap credentials.
export DJANGO_RUN_MIGRATIONS=false
export DJANGO_BOOTSTRAP_SUPERADMIN=false
compose=(docker compose --project-directory "$repo_root" -f "$compose_file")
"${compose[@]}" config --quiet

# Preserve runtime configuration during this code-only update. The override stays
# in memory/stdin; no credentials are written to a temporary file or printed.
containers=()
for service in "${services[@]}"; do
  container=$("${compose[@]}" ps -aq "$service")
  [[ -z "$container" ]] || containers+=("$container")
done
runtime_override='{"services":{}}'
if (( ${#containers[@]} )); then
  runtime_override=$(docker inspect "${containers[@]}" | python3 -c '
import json, sys
services = {}
for container in json.load(sys.stdin):
    name = container["Config"]["Labels"]["com.docker.compose.service"]
    env = dict(value.split("=", 1) for value in container["Config"]["Env"])
    env["DJANGO_RUN_MIGRATIONS"] = "false"
    env["DJANGO_BOOTSTRAP_SUPERADMIN"] = "false"
    # Compose interpolates dollar signs in JSON/YAML values too.
    env = {key: value.replace("$", "$") for key, value in env.items()}
    services[name] = {"environment": env}
print(json.dumps({"services": services}))
')
fi

# Pin every service in the in-memory override, including a not-yet-created manage.
# Older Compose files may still declare a separate build for some workers.
runtime_override=$(printf '%s\n' "$runtime_override" | python3 -c '
import json, os, sys
override = json.load(sys.stdin)
for service in sys.argv[1:]:
    override["services"].setdefault(service, {})["image"] = os.environ["CONFRONTA_APP_IMAGE"]
print(json.dumps(override))
' "${services[@]}")

declare -A credential_hashes
while read -r service digest; do
  credential_hashes["$service"]=$digest
done < <(printf '%s\n' "$runtime_override" | python3 -c '
import hashlib, json, re, sys
for name, config in json.load(sys.stdin)["services"].items():
    env = {k: v.replace("$", "$") for k, v in config["environment"].items()
           if re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", k)}
    print(name, hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest())
')
printf '%s\n' "$runtime_override" | "${compose[@]}" -f - up -d --no-deps --no-build --pull never --force-recreate \
  --wait --wait-timeout 60 "${services[@]}"
unset runtime_override

source_hash=''
for service in "${services[@]}"; do
  container=$("${compose[@]}" ps -q "$service")
  [[ -n "$container" ]] || { echo "Missing container: $service" >&2; exit 1; }
  actual_image=$(docker inspect --format '{{.Image}}' "$container")
  [[ "$actual_image" == "$image_id" ]] || {
    echo "Image mismatch: $service" >&2
    exit 1
  }

  if [[ -n "${credential_hashes[$service]:-}" ]]; then
    actual_credentials=$(docker exec "$container" python -B -c '
import hashlib, json, os, re
env = {k: v for k, v in os.environ.items() if re.search(r"PASSWORD|SECRET|TOKEN|API_KEY", k)}
print(hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest())
')
    [[ "$actual_credentials" == "${credential_hashes[$service]}" ]] || {
      echo "Runtime credential mismatch: $service" >&2
      exit 1
    }
  fi
  actual_hash=$(docker exec "$container" python -B -c '
import hashlib
from pathlib import Path
root = Path("/app")
paths = [root / "manage.py"]
for name in ("config", "administracao", "aplicativo", "billing"):
    paths.extend((root / name).rglob("*.py"))
digest = hashlib.sha256()
for path in sorted(paths):
    digest.update(str(path.relative_to(root)).encode() + b"\0")
    digest.update(path.read_bytes() + b"\0")
print(digest.hexdigest())
')
  if [[ -n "$source_hash" && "$actual_hash" != "$source_hash" ]]; then
    echo "Django source mismatch: $service" >&2
    exit 1
  fi
  source_hash=$actual_hash
  echo "$service image=$actual_image django_sha256=$actual_hash"
done
