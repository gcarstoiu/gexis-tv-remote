#!/bin/sh
# Build dist/<id>-<version>.tar.gz with plugin.json at its top level. The same
# files give the same bytes, so a listed archive's sha256 can be checked
# against the tag it was built from.
set -eu
cd "$(dirname "$0")"
mkdir -p dist
dir=tv-remote
id=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["id"])' "$dir/plugin.json")
version=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$dir/plugin.json")
out="dist/$id-$version.tar.gz"
files=$(cd "$dir" && git ls-files)
(cd "$dir" && tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    --mode=u+w,go-w -cf - $files) | gzip -n -9 > "$out"
echo "$out $(stat -c %s "$out") bytes, sha256 $(sha256sum "$out" | cut -d' ' -f1)"
