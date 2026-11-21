#!/bin/bash
# Keep a copy of each build download from a host other than GitHub (scripts/linux/deps.sh --outside-sources: dav1d, Tcl,
# Tk, FFmpeg) on this repository's linux-parts release, which scripts/linux/deps.sh and scripts/windows/dav1d.sh fetch
# first. A copy already there is left; a new one is fetched from its host, checked as the build checks it (its SHA-256,
# or what it unpacks to), and uploaded only then. Run from the Linux release job (GH_TOKEN, GITHUB_REPOSITORY), or by
# hand with gh signed in. A copy that cannot be fetched is said and skipped: the build then takes the host's, as before.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=${GITHUB_REPOSITORY:-notquiteog/Hookshot}
TAG=linux-parts
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT

have=$(gh release view "$TAG" --repo "$REPO" --json assets --jq '.assets[].name')
tree_of() {   # folder -> the SHA-256 of its files by name and content (scripts/linux/deps.sh tree_is); nothing for a link
    [ -z "$(find "$1" ! -type f ! -type d -print -quit)" ] || return 1
    (cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
}
missing=0
while read -r name url check; do
    if printf '%s\n' "$have" | grep -qxF "$name"; then
        echo "on $TAG: $name"
        continue
    fi
    if ! curl -fsSL --retry 5 --retry-delay 10 --proto '=https' -o "$W/$name" "$url"; then
        echo "::warning::$name: its host did not answer; not copied this time ($url)"
        missing=1
        continue
    fi
    case $check in
        sha256:*) got=$(sha256sum "$W/$name" | cut -d' ' -f1); want=${check#sha256:} ;;
        tree:*)
            rm -rf "$W/src" && mkdir -p "$W/src" && tar -xf "$W/$name" -C "$W/src" --strip-components=1
            got=$(tree_of "$W/src" || echo link); want=${check#tree:} ;;
        *) got=-; want=- ;;
    esac
    if [ "$got" != "$want" ]; then
        echo "::error::$name from $url is not the pinned download ($got, expected $want); not copied"
        exit 1
    fi
    gh release upload "$TAG" "$W/$name" --repo "$REPO"
    echo "copied to $TAG: $name"
done < <(scripts/linux/deps.sh --outside-sources)
[ "$missing" = 0 ] || echo "(a host that did not answer is tried again on the next run)"
