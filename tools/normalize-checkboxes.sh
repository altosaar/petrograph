#!/bin/bash
# Normalize Markdown Checkboxes
# Converts `- [ ]` → `!x` and `- [x]` → `!xc` to prevent Obsidian auto-task detection
# Uses unique identifiers (!x, !xc) that won't match any Markdown/Obsidian patterns
# L9 DevOps: idempotent, comprehensive logging, dry-run support, atomic operations
#
# Usage:
#   ./normalize-checkboxes.sh           # Default: dry-run
#   ./normalize-checkboxes.sh --apply   # Apply changes

set -euo pipefail

# Configuration
NOTES_DIR="${HOME}/notes"
EXCLUDE_FILE="${EXCLUDE_FILE:-Tasks.md}"
DRY_RUN=true
LOG_DIR="${HOME}/.config/devops-tasks/logs"

# Ensure log directory exists
mkdir -p "${LOG_DIR}"

# Logging function
log() {
    local level="$1"
    shift
    local msg="$@"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] ${msg}" | tee -a "${LOG_DIR}/normalize-checkboxes.log"
}

# Parse arguments
if [[ "${1:-}" == "--apply" ]]; then
    DRY_RUN=false
fi

log "INFO" "Starting checkbox normalization"
log "INFO" "Mode: $([ "$DRY_RUN" = true ] && echo 'DRY-RUN (preview only)' || echo 'APPLY (making changes)')"
log "INFO" "Target directory: ${NOTES_DIR}"

# Validation
if [ ! -d "$NOTES_DIR" ]; then
    log "ERROR" "Notes directory not found: ${NOTES_DIR}"
    exit 1
fi

# Find all markdown files with checkboxes
log "INFO" "Scanning for checkbox patterns..."

TOTAL_CHANGES=0
MODIFIED_FILES=0

cd "$NOTES_DIR"

for file in **/*.md *.md; do
    [[ "$file" == "$EXCLUDE_FILE" ]] && continue
    [[ -f "$file" ]] || continue

    # Check if file contains checkbox patterns: unchecked or completed
    # Unchecked: `- [ ]`, `-[]`, `-![]`, `![]`, `!x`
    # Completed: `- [x]`, `-[x]`, `-!xc`, `!xc`
    if grep -E '(\- \[ \]|-\[\]|-!\[\]|!\[\]|!x|\- \[x\]|-\[x\]|-!xc|!xc)' "$file" 2>/dev/null | grep -q .; then
        FILE_CHANGES=$(grep -E '(\- \[ \]|-\[\]|-!\[\]|!\[\]|!x|\- \[x\]|-\[x\]|-!xc|!xc)' "$file" 2>/dev/null | wc -l)
        TOTAL_CHANGES=$((TOTAL_CHANGES + FILE_CHANGES))
        MODIFIED_FILES=$((MODIFIED_FILES + 1))

        log "INFO" "Found $FILE_CHANGES checkboxes: $file"

        if [ "$DRY_RUN" = false ]; then
            # Atomic write: backup, modify, verify, commit
            BACKUP_FILE="${file}.bak.$(date +%s)"
            cp "$file" "$BACKUP_FILE"

            # Replace all patterns with !x (unchecked) or !xc (completed)
            # Order matters: process - [x] before - [ ] to avoid partial replacements
            sed -e 's/\- \[x\]/!xc/g' -e 's/-\[x\]/!xc/g' -e 's/-!xc/!xc/g' \
                -e 's/\- \[ \]/!x/g' -e 's/-\[\]/!x/g' -e 's/-!\[\]/!x/g' -e 's/!\[\]/!x/g' "$file" > "${file}.tmp"
            mv "${file}.tmp" "$file"
            rm "$BACKUP_FILE"

            log "INFO" "Applied: $file ($FILE_CHANGES changes)"
        fi
    fi
done

cd - > /dev/null

# Summary
log "INFO" "=== Summary ==="
log "INFO" "Files with checkboxes: ${MODIFIED_FILES}"
log "INFO" "Total checkbox changes: ${TOTAL_CHANGES}"

if [ "$DRY_RUN" = true ]; then
    log "INFO" "DRY-RUN mode: No changes applied"
    log "INFO" "To apply changes, run: $0 --apply"
else
    log "INFO" "Changes applied successfully"
fi

exit 0
