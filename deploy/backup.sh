#!/bin/sh
# Nightly: database dump and recorded ticks to an off-server bucket. Test a restore monthly.
# Needs BACKUP_BUCKET (s3://...), and aws credentials for an S3-compatible store.
set -eu

stamp=$(date +%Y-%m-%d)
folder=$(mktemp -d)
trap 'rm -rf "$folder"' EXIT

compose="docker compose -f $(dirname "$0")/docker-compose.yml"
$compose exec -T postgres pg_dump -U coolcandle coolcandle | gzip > "$folder/coolcandle-$stamp.sql.gz"

aws s3 cp "$folder/coolcandle-$stamp.sql.gz" "$BACKUP_BUCKET/db/"
$compose exec -T api tar -C /data -cf - ticks | aws s3 cp - "$BACKUP_BUCKET/ticks/ticks-$stamp.tar"
