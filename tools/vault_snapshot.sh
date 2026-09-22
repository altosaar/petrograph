#!/usr/bin/env bash
# Snapshot the vault's markdown into a local git mirror, one commit per run.
#
# Obsidian's File Recovery is the only line-level history this machine keeps, and it prunes
# (keepDays, 60 here). That is why a 12-month corpus can only ever be approximated from the
# vault's current state — the older diffs were never retained. This fixes that going forward:
# once the mirror has two commits, `git log -p --since=12.months` is a real, unbounded diff
# history, and vault_corpus.py prefers it over every approximation.
#
# The mirror holds markdown only, lives outside the vault and outside this repo (it is private
# note content), and is never pushed anywhere.
#
#   ./tools/vault_snapshot.sh              # snapshot ~/notes
#   ./tools/vault_snapshot.sh --vault-dir ~/other-vault --mirror ~/somewhere
set -euo pipefail

VAULT="${VAULT_DIR:-$HOME/notes}"
MIRROR="${PETROGRAPH_VAULT_HISTORY:-$HOME/.local/share/petrograph/vault-history}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vault-dir) VAULT="$2"; shift 2 ;;
        --mirror)    MIRROR="$2"; shift 2 ;;
        -h|--help)   sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)           echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

# ~/notes is a symlink into iCloud; resolve it so rsync copies contents, not the link.
VAULT="$(cd "$VAULT" && pwd -P)"
[[ -d "$VAULT" ]] || { echo "Vault not found: $VAULT" >&2; exit 1; }

mkdir -p "$MIRROR"
if [[ ! -d "$MIRROR/.git" ]]; then
    git -C "$MIRROR" init -q
    git -C "$MIRROR" config user.name  "petrograph vault_snapshot"
    git -C "$MIRROR" config user.email "vault-snapshot@localhost"
    printf 'Private mirror of %s — markdown only, never pushed.\n' "$VAULT" > "$MIRROR/README"
    echo "Initialized mirror at $MIRROR"
fi

# Markdown only: keep directories so rsync can descend, take *.md, drop everything else.
rsync -a --delete --prune-empty-dirs \
    --exclude '.obsidian/' --exclude '.trash/' --exclude '.git/' \
    --include '*/' --include '*.md' --exclude '*' \
    "$VAULT"/ "$MIRROR"/

git -C "$MIRROR" add -A
if git -C "$MIRROR" diff --cached --quiet; then
    echo "No markdown changes since the last snapshot — nothing committed."
else
    git -C "$MIRROR" commit -q -m "snapshot $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Committed $(git -C "$MIRROR" show --stat --oneline HEAD | tail -1 | xargs)"
fi

count=$(git -C "$MIRROR" rev-list --count HEAD)
printf 'Mirror: %s — %s commit(s), %s markdown files.\n' \
    "$MIRROR" "$count" "$(find "$MIRROR" -name '*.md' -not -path '*/.git/*' | wc -l | xargs)"
if [[ "$count" -lt 2 ]]; then
    echo "This is the baseline commit — it is not treated as 'added text'. Real diffs start"
    echo "at the next snapshot, so run this on whatever cadence you want history at."
fi
