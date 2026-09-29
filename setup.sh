#!/usr/bin/env bash
# setup.sh — Kit environment setup (run once after cloning/copying this kit)
#
# Option A (project-scoped, default):
#   ./setup.sh                          # auto-detect projects root
#   ./setup.sh /path/to/projects-root   # explicit projects root
#   Kit stays in place; hooks resolve via $SCRIPT_DIR/.claude/hooks.
#
# Option B (global):
#   ./setup.sh --global
#   Copies the kit's .claude/ tree into $HOME/.claude/ on first install,
#   then substitutes ${KIT_HOOKS_DIR} placeholders to absolute paths.
#
# Both modes:
#   - Substitute ${KIT_HOOKS_DIR} placeholders in settings.json + hooks/*.sh
#     to the resolved absolute hook directory.
#   - Write a .install-mode marker; refuse mode flip on re-run.
#   - Re-running in the same mode is a no-op (idempotent; preserves user mods).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 0. Argument parsing ---
INSTALL_MODE="project"
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --global) INSTALL_MODE="global"; shift ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done
# bash 3.2-safe positional-arg restore:
# "${EXTRA_ARGS[@]:-}" expands to one empty string on empty arrays under bash 3.2
# (macOS default), corrupting positional-arg pass-through. Guard explicitly.
if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
    set -- "${EXTRA_ARGS[@]}"
else
    set --
fi

echo "=== Claude Code Kit Setup ==="
echo "  Mode: $INSTALL_MODE"
echo ""

# --- 1. Resolve KIT_HOOKS_DIR + INSTALL_ROOT per mode ---
if [[ "$INSTALL_MODE" == "global" ]]; then
    # P0.9/M3: honour CLAUDE_CONFIG_DIR. This hard-coded $HOME/.claude before, and
    # read CLAUDE_CONFIG_DIR nowhere — which meant setup.sh could not be pointed at a
    # sandbox, so it could not be TESTED without writing to the real config. The
    # consent step below edits the user's CLAUDE.md, so that was not a theoretical
    # problem: a CI run would have edited the author's own.
    INSTALL_ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
    KIT_HOOKS_DIR="$INSTALL_ROOT/hooks"
    mkdir -p "$INSTALL_ROOT"
else
    # Option A: kit stays in place; INSTALL_ROOT is SCRIPT_DIR/.claude
    INSTALL_ROOT="$SCRIPT_DIR/.claude"
    KIT_HOOKS_DIR="$INSTALL_ROOT/hooks"
fi

# --- 2. Mode-flip detection: refuse if a different-mode marker exists ---
MODE_MARKER="$INSTALL_ROOT/.install-mode"
IS_FIRST_INSTALL=0
if [[ -f "$MODE_MARKER" ]]; then
    PRIOR_MODE="$(cat "$MODE_MARKER")"
    if [[ "$PRIOR_MODE" != "$INSTALL_MODE" ]]; then
        echo "ERROR: existing install at $INSTALL_ROOT is mode=$PRIOR_MODE;" >&2
        echo "       cannot flip to mode=$INSTALL_MODE." >&2
        echo "       Remove $INSTALL_ROOT and re-run, or re-run with the same mode." >&2
        exit 2
    fi
else
    IS_FIRST_INSTALL=1
fi

# --- 3. Option B: copy kit's .claude/ tree into $HOME/.claude/ (first install only) ---
# Guarded by IS_FIRST_INSTALL — re-running with --global is a no-op that preserves
# any user modifications under $HOME/.claude/.
if [[ "$INSTALL_MODE" == "global" && "$IS_FIRST_INSTALL" == "1" ]]; then
    if [[ -d "$SCRIPT_DIR/.claude" ]]; then
        cp -R "$SCRIPT_DIR/.claude/." "$INSTALL_ROOT/"
        echo "Copied: $SCRIPT_DIR/.claude/ -> $INSTALL_ROOT/"
    else
        echo "ERROR: $SCRIPT_DIR/.claude not found — cannot complete --global install." >&2
        exit 2
    fi
fi

echo "$INSTALL_MODE" > "$MODE_MARKER"

# --- 4. Substitute ${KIT_HOOKS_DIR} placeholders (idempotent within a mode) ---
substitute_placeholder() {
    local file="$1"
    local tmp
    tmp="$(mktemp)"
    # Use | as sed delimiter (paths are unlikely to contain it).
    sed "s|\${KIT_HOOKS_DIR}|${KIT_HOOKS_DIR}|g" "$file" > "$tmp" && mv "$tmp" "$file"
}

if [[ -f "$INSTALL_ROOT/settings.json" ]]; then
    substitute_placeholder "$INSTALL_ROOT/settings.json"
fi
if [[ -d "$INSTALL_ROOT/hooks" ]]; then
    for hook in "$INSTALL_ROOT"/hooks/*.sh; do
        [[ -f "$hook" ]] && substitute_placeholder "$hook"
    done
fi

echo "Resolved hook directory: $KIT_HOOKS_DIR"
echo ""

# --- 4b. The three always-on rules: ASK, never write unasked (P0.9 / O1) ---
#
# ~/.claude/rules/ is not an auto-load location. Exactly three rules are reachable
# only via an explicit @ import — no skill and no hook references them by path — so
# without these lines the framework loses its grounding, its investigate-first
# discipline and its destructive-operation policy.
#
# TTY-guarded (P0.9/M4): --global is the only non-interactive mode, so an
# unconditional prompt here would block CI on stdin. `--assume-no` skips it outright.
Q_IMPORTS=("@rules/grounding.md" "@rules/safe-defaults.md" "@rules/subagent-tools.md")
USER_CLAUDE_MD="$INSTALL_ROOT/CLAUDE.md"

missing_imports() {
    local m=()
    for imp in "${Q_IMPORTS[@]}"; do
        if [[ ! -f "$USER_CLAUDE_MD" ]] || ! grep -qF "$imp" "$USER_CLAUDE_MD"; then
            m+=("$imp")
        fi
    done
    printf '%s\n' "${m[@]:-}"
}

append_imports() {
    {
        printf '\n<!-- Q framework — required always-on rules -->\n'
        printf '%s\n' "${Q_IMPORTS[@]}"
    } >> "$USER_CLAUDE_MD"
    echo "  appended to $USER_CLAUDE_MD"
}

MISSING="$(missing_imports | grep -c '@rules' || true)"
if [[ "$MISSING" -eq 0 ]]; then
    echo "Always-on rules: already imported in $USER_CLAUDE_MD"
elif [[ "${Q_ACCEPT_RULES:-0}" == "1" ]]; then
    # Consent given ahead of time rather than at a prompt — for a scripted install,
    # and the only way this branch is reachable without a TTY. Still EXPLICIT: the
    # operator sets the variable deliberately, the same shape as the house
    # ALLOW_OUT_OF_TREE=1 override. Never defaulted on.
    echo "Always-on rules: Q_ACCEPT_RULES=1 — appending without prompting"
    append_imports
elif [[ "${ASSUME_NO:-0}" == "1" ]] || [[ ! -t 0 ]]; then
    echo "Always-on rules: NOT configured. Add these three lines to $USER_CLAUDE_MD:"
    printf '    %s\n' "${Q_IMPORTS[@]}"
    echo "  (skipped: no TTY or --assume-no. Nothing was written.)"
else
    echo "Q needs three lines in $USER_CLAUDE_MD so its always-on rules load"
    echo "each session. I will append exactly this and change nothing else:"
    echo ""
    printf '    %s\n' "${Q_IMPORTS[@]}"
    echo ""
    read -r -p "Append them? [y]es / [s]how me / [n]o: " ANS || ANS="n"
    case "$ANS" in
        y|Y) append_imports ;;
        s|S)
            echo "  add the three lines above to: $USER_CLAUDE_MD"
            ;;
        *)
            echo "  skipped. Without them: file I/O goes through Bash and trips the"
            echo "  sandbox guards, investigate-before-answering is not applied, and"
            echo "  the destructive-operation policy is not loaded. Recoverable at"
            echo "  any time — re-run this script."
            ;;
    esac
fi
echo ""

# --- 4c. Verification thoroughness (D16) ---
#
# Only `thorough` is a VOTE (three checkers that must agree); the rest are one
# isolated opinion. Every tier still spawns an isolated checker that is not the
# producer — that floor is not on the dial, and neither are the fixed pipelines.
if [[ -n "${Q_VALIDATION_RIGOR:-}" ]]; then
    echo "Verification rigor: $Q_VALIDATION_RIGOR (from the environment)"
elif [[ "${ASSUME_NO:-0}" == "1" ]] || [[ ! -t 0 ]]; then
    echo "Verification rigor: defaulting to 'standard'. Override with"
    echo "  export Q_VALIDATION_RIGOR=thorough|standard|light|minimal"
else
    echo "How thorough should verification be?"
    echo "  thorough  3 Sonnet must agree + an Opus advisory   (a vote; highest cost)"
    echo "  standard  1 Sonnet + an Opus advisory              (default)"
    echo "  light     a single Opus checker                    (no vote)"
    echo "  minimal   a single Sonnet checker                  (cheapest that verifies)"
    read -r -p "Tier [standard]: " TIER || TIER=""
    TIER="${TIER:-standard}"
    case "$TIER" in
        thorough|standard|light|minimal) ;;
        *) echo "  unknown tier '$TIER' — using 'standard'"; TIER="standard" ;;
    esac
    echo "  add this to your shell profile:"
    echo "    export Q_VALIDATION_RIGOR=$TIER"
fi
echo ""

# --- 5. Option A: locate projects root + write kit.env ---
# Skipped under --global (global install doesn't pin a single projects root).
if [[ "$INSTALL_MODE" == "project" ]]; then
    PROJECTS_ROOT=""
    DETECTION_METHOD=""

    if [[ -n "${1:-}" ]]; then
        PROJECTS_ROOT="${1%/}"
        DETECTION_METHOD="argument"
    elif [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
        PROJECTS_ROOT="${CLAUDE_PROJECT_DIR%/}"
        DETECTION_METHOD="\$CLAUDE_PROJECT_DIR"
    else
        # Walk up from the kit directory looking for CLAUDE.md or .git
        WALK="$SCRIPT_DIR"
        while [[ "$WALK" != "/" && "$WALK" != "." ]]; do
            if [[ -f "$WALK/CLAUDE.md" ]] || [[ -d "$WALK/.git" ]]; then
                PROJECTS_ROOT="$WALK"
                DETECTION_METHOD="auto (found CLAUDE.md/.git)"
                break
            fi
            WALK="$(dirname "$WALK")"
        done
    fi

    if [[ -z "$PROJECTS_ROOT" ]]; then
        PROJECTS_ROOT="$SCRIPT_DIR"
        DETECTION_METHOD="fallback (kit directory)"
    fi

    if [[ -d "$HOME/.claude" ]]; then
        DOTCLAUDE_STATUS="found at $HOME/.claude"
    else
        DOTCLAUDE_STATUS="not found — kit runs project-scoped"
    fi

    echo "Detected configuration:"
    echo "  Projects root   : $PROJECTS_ROOT  ($DETECTION_METHOD)"
    echo "  \$HOME/.claude  : $DOTCLAUDE_STATUS"
    echo ""

    read -r -p "Confirm? [y / enter new path / n to abort]: " ANSWER
    ANSWER_LOWER="$(echo "$ANSWER" | tr '[:upper:]' '[:lower:]')"
    case "$ANSWER_LOWER" in
        y|yes|"")
            ;;
        n|no)
            echo "Aborted. Re-run with: ./setup.sh /your/projects-root"
            exit 1
            ;;
        *)
            PROJECTS_ROOT="${ANSWER%/}"
            echo "Using: $PROJECTS_ROOT"
            ;;
    esac

    if [[ ! -d "$PROJECTS_ROOT" ]]; then
        echo ""
        echo "WARNING: directory does not exist: $PROJECTS_ROOT"
        read -r -p "Create it? [y/N]: " CREATE
        CREATE_LOWER="$(echo "$CREATE" | tr '[:upper:]' '[:lower:]')"
        case "$CREATE_LOWER" in
            y|yes)
                mkdir -p "$PROJECTS_ROOT"
                echo "Created: $PROJECTS_ROOT"
                ;;
            *)
                echo "Aborted — path must exist before setup."
                exit 1
                ;;
        esac
    fi

    KIT_ENV="$SCRIPT_DIR/kit.env"
    TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > "$KIT_ENV" <<EOF
# kit.env — generated by setup.sh on $TIMESTAMP
# Source this file or ensure CLAUDE_PROJECT_DIR is set in your shell profile.
# Claude Code reads this automatically when starting a session in this directory.
CLAUDE_PROJECT_DIR=$PROJECTS_ROOT
EOF

    echo ""
    echo "Written: $KIT_ENV"
    echo ""
    echo "Optional — add to your shell profile (~/.zshrc or ~/.bashrc):"
    echo "  export CLAUDE_PROJECT_DIR=\"$PROJECTS_ROOT\""
fi

echo ""
echo "=== Next steps ==="
echo "  1. Edit CLAUDE.md — fill in [Work Context] and [Personal Context] sections"
echo "  2. Start Claude Code here: claude"
echo "  3. Run /session-start to begin your first session"
