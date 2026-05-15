#!/usr/bin/env bash
# Write a note into an MR, or keep the one thread we open ourselves: a reply in
# a thread, a thread of our own, a review comment on a line of the diff,
# `--resolve`, `--unresolve`, and `--show` and `--edit` of the note at the head
# of our thread. Records the note as `<note id> <writer tag>` and ends it with the writer's
# marker - `mr_watch/threads.py` spells both - so a watch on another machine, which holds no
# record, still reads the note back as ours.
#
# The watches decide what still awaits an answer by these records, so anything
# we post without going through here looks like somebody else's comment and is
# re-surfaced forever. That holds for the thread head too: the test plan
# (`modes/build.md`) is a thread we open ourselves. Body comes from a file or
# stdin, keeping quoting out of the way. Prints the discussion id, which is what
# a later reply needs; `--edit` prints the note it rewrote and `--show` the body
# it read.
#
#   scripts/mr-reply.sh 348 <discussion-id> reply.md
#   scripts/mr-reply.sh 348 --new test-plan.md
#   scripts/mr-reply.sh 348 --line path/to/module.py:64 finding.md
#   scripts/mr-reply.sh 348 --line path/to/module.py:60-64 finding.md
#   scripts/mr-reply.sh 348 --resolve <discussion-id>
#   scripts/mr-reply.sh 348 --unresolve <discussion-id>
#   scripts/mr-reply.sh 348 --show <discussion-id> >plan.md
#   scripts/mr-reply.sh 348 --edit <discussion-id> plan.md
set -euo pipefail

usage='usage: mr-reply.sh <mr-iid> <discussion-id|--new|--line path:line> [body-file]
       mr-reply.sh <mr-iid> <--resolve|--unresolve|--show|--edit> <discussion-id> [body-file]'
mr=${1:?$usage}
target=${2:?$usage}
# The project is the one this checkout pushes to, the same way the watches take it,
# so nothing here carries a repository name of its own.
project=${MR_PROJECT:-}
if [ -z "$project" ]; then
  url=$(git config --get remote.origin.url 2>/dev/null || true)
  url=${url%.git}
  case "$url" in
    *://*)
      project=${url#*://}
      project=${project#*/}
      ;;
    *@*:*) project=${url#*:} ;;
    *) project=$url ;;
  esac
fi
if [ -z "$project" ]; then
  echo 'mr-reply: no project - set MR_PROJECT or give the checkout an origin remote' >&2
  exit 1
fi
notes_file=${MR_AGENT_NOTES:-$HOME/.agent-toolkit/mr-agent-notes}
# Same default as the watches: the working-tree root, which a session owns.
writer_tag=${MR_AGENT_TAG:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}

# The mode decides which argument is the body: `--line` and `--edit` spend the
# third on the place and the thread, so reading it before that turns them into a
# file nobody can open.
case "$target" in
  --line | --edit) body_file=${4:-} ;;
  --resolve | --unresolve | --show) body_file= ;;
  *) body_file=${3:-} ;;
esac
encoded=${project//\//%2F}
base="projects/$encoded/merge_requests/$mr"

# A note is recorded when we write it, so one missing from the file is not ours.
ours() {
  grep -qxF "$1 $writer_tag" "$notes_file" 2>/dev/null
}

# `--resolve` counts the plan in the thread, the rest take the note at its head.
load_thread() {
  thread=$(glab api "$base/discussions/$1")
  head_note=$(printf '%s' "$thread" | jq -r '.notes[0].id')
}

# A snapshot of ours drops the ticks a human put in, so the plan is read back
# before it is rewritten; reading asks nothing about who owns the thread.
if [ "$target" = "--show" ]; then
  load_thread "${3:?$usage}"
  printf '%s' "$thread" | jq -r '.notes[0].body'
  exit 0
fi

# A check over a pipe reads it to the end (`grep >/dev/null`, not `grep -q`): under
# pipefail a writer cut off by an early `-q` exit fails the whole pipe, and the check
# reads as no match now and then.
# Cyrillic in UTF-8 starts with one of two lead bytes; a byte match holds in any
# locale, where a `[А-Я]` range does not.
cyrillic=$'[\xd0\xd1]'
mr_json=$(glab api "$base")
mr_text=$(printf '%s' "$mr_json" | jq -r '(.title // "") + "\n" + (.description // "")')
# A fenced block may quote a log or a screen of ours, and those are not English.
if printf '%s\n' "$mr_text" | awk '/^ *```/ { fence = !fence; next } !fence' |
  LC_ALL=C grep "$cyrillic" >/dev/null; then
  case "$writer_tag" in
    # Reviewing, we write in somebody's MR and do not edit its title.
    review-*) echo "mr-reply: the title or the description of this MR is not English" >&2 ;;
    *)
      echo "mr-reply: the title or the description of the MR is not English - rewrite them" >&2
      exit 1
      ;;
  esac
fi

# A resolve over unticked items passes the plan off as done.
if [ "$target" = "--resolve" ]; then
  discussion=${3:?"usage: mr-reply.sh <mr-iid> --resolve <discussion-id>"}
  load_thread "$discussion"
  # Somebody else's thread is resolved by its author, who checks the fix.
  if ! ours "$head_note"; then
    echo "mr-reply: thread $discussion is not ours - its author resolves it" >&2
    exit 1
  fi
  plan=$(printf '%s' "$thread" | jq -r '[.notes[].body] | join("\n")')
  left=$(printf '%s' "$plan" | grep -c '^[[:space:]]*- \[ \]' || true)
  if [ "$left" -gt 0 ]; then
    echo "mr-reply: items left unchecked in the thread: $left" >&2
    exit 1
  fi
  # A tick without a sha does not say which state of the branch it is true for.
  # Seven hex characters spelled out: an awk without interval expressions reads
  # `{7,}` as its own text.
  nosha=$(printf '%s' "$plan" | awk '
    /^[*[:space:]]*Ручны/ { manual = 1 }
    /^[*[:space:]]*Автоматическ/ { manual = 0 }
    !manual && /^[[:space:]]*- \[x\]/ {
      tail = $0
      sub(/[[:space:]]+$/, "", tail)
      if (tail !~ / - /) { count++; next }
      sub(/^.* - /, "", tail)
      if (tail !~ /^[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*$/) { count++ }
    }
    END { print count + 0 }')
  if [ "$nosha" -gt 0 ]; then
    echo "mr-reply: automatic ticks naming no run: $nosha" >&2
    exit 1
  fi
  glab api --method PUT "$base/discussions/$discussion?resolved=true" >/dev/null
  echo "$discussion"
  exit 0
fi

# A thread goes back to open because a new check appeared, and that check starts
# unticked: counting boxes here would refuse every re-opening.
if [ "$target" = "--unresolve" ]; then
  discussion=${3:?"usage: mr-reply.sh <mr-iid> --unresolve <discussion-id>"}
  load_thread "$discussion"
  if ! ours "$head_note"; then
    echo "mr-reply: thread $discussion is not ours - its author re-opens it" >&2
    exit 1
  fi
  glab api --method PUT "$base/discussions/$discussion?resolved=false" >/dev/null
  echo "$discussion"
  exit 0
fi

# The file records every note we write, replies included, so the thread names the
# one to edit: the plan is counted in the note at its head and nowhere else.
if [ "$target" = "--edit" ]; then
  discussion=${3:?"usage: mr-reply.sh <mr-iid> --edit <discussion-id> [body-file]"}
  load_thread "$discussion"
  if ! ours "$head_note"; then
    echo "mr-reply: thread $discussion is not ours - its writer edits its head note" >&2
    exit 1
  fi
fi

# Named and missing, the file is an error; stdin is read only when none is named,
# or a path with a typo in it reads the terminal and passes for an empty body.
if [ -n "$body_file" ]; then
  body=$(cat "$body_file")
else
  body=$(cat)
fi

# An empty body over the head note takes the plan with it, ticks of a human and all.
# A marker is no body either: the one read back with `--show` ends with it.
if [ "$target" = "--edit" ]; then
  written=$(printf '%s' "$body" | sed 's/<!-- agent-toolkit:[0-9a-f]* -->//g' | tr -d '[:space:]')
  if [ -z "$written" ]; then
    echo "mr-reply: an empty body would wipe the note - write the plan into it" >&2
    exit 1
  fi
fi

# A long dash is not written in our texts.
if printf '%s' "$body" | LC_ALL=C grep -F -e '—' -e '–' >/dev/null; then
  echo "mr-reply: a long dash in the body - write a plain hyphen" >&2
  exit 1
fi

# A note is read, not scrolled: past the limit it is cut down, or the reason for
# the length is named in MR_ALLOW_LONG, and even then not past the ceiling. The
# head note of a plan is a checklist, not a comment, and is not counted.
max_len=${MR_MAX_NOTE_LEN:-1500}
hard_len=${MR_MAX_NOTE_LEN_HARD:-6000}
len=$(printf '%s' "$body" | jq -Rs length)
plan_head=
case "$target" in
  --new | --edit)
    if printf '%s' "$body" | grep '^[[:space:]]*- \[[ x]\]' >/dev/null; then plan_head=1; fi
    ;;
esac
if [ -z "$plan_head" ] && [ "$len" -gt "$max_len" ]; then
  reason=${MR_ALLOW_LONG:-}
  if [ "${#reason}" -lt 25 ] || [ "${reason#* }" = "$reason" ]; then
    echo "mr-reply: the note is $len characters, the limit is $max_len - cut it down, or name the reason for a long one in MR_ALLOW_LONG" >&2
    exit 1
  fi
  if [ "$len" -gt "$hard_len" ]; then
    echo "mr-reply: the note is $len characters, over the ceiling of $hard_len even with a reason - move the detail into the issue or the MR description and link it" >&2
    exit 1
  fi
fi

# The marker for the human and for watches without a record: the script adds it,
# so it cannot be forgotten, and it is spelled once, in mr_watch/threads.py.
marker=$(MR_AGENT_TAG="$writer_tag" python3 "$(dirname "${BASH_SOURCE[0]}")/mr_watch/threads.py")
case "$body" in
  *"$marker") ;;
  *) body="$body"$'\n\n'"$marker" ;;
esac

# A plan smeared over replies is one nobody counts.
case "$target" in
  --new | --line | --edit) ;;
  *)
    if printf '%s' "$body" | grep '^[[:space:]]*- \[[ x]\]' >/dev/null; then
      echo "mr-reply: boxes belong in the head note of the thread, not in a reply" >&2
      exit 1
    fi
    ;;
esac

if [ "$target" = "--edit" ]; then
  glab api --method PUT "$base/notes/$head_note" -f body="$body" >/dev/null
  echo "$head_note"
  exit 0
fi

post_inline() {
  # `-f "position[...]"` drops the nested fields and the comment lands as a plain
  # note, off the line it was about, so the position travels as a JSON body.
  # A range names its first and last line by GitLab's line code,
  # `<sha1 of the path>_<old line>_<new line>`, the old side empty for a new line.
  jq -n --arg body "$body" --arg path "$1" --argjson line "$2" --argjson old "$3" \
    --arg oldpath "$old_path" --arg first "$first" --arg code "$path_code" \
    --arg base "$base_sha" --arg start "$start_sha" --arg head "$head_sha" \
    '($old // null) as $o |
    (if $o == null then 0 else $o - $line end) as $shift |
    {body: $body, position: ({position_type: "text", base_sha: $base,
      start_sha: $start, head_sha: $head, new_path: $path, old_path: $oldpath,
      new_line: $line} + (if $o == null then {} else {old_line: $o} end)
      + (if $first == "" then {} else {line_range: {
          start: {line_code: "\($code)_\(if $o == null then "" else ($first | tonumber) + $shift end)_\($first)",
                  type: (if $o == null then "new" else null end), new_line: ($first | tonumber)},
          end: {line_code: "\($code)_\(if $o == null then "" else $o end)_\($line)",
                type: (if $o == null then "new" else null end), new_line: $line}}} end))}' |
    glab api --method POST --header "Content-Type: application/json" "$base/discussions" --input -
}

case "$target" in
  --new) created=$(glab api -X POST "$base/discussions" -f body="$body") ;;
  --line)
    path=${3:?$usage}
    line=${path##*:}
    path=${path%:*}
    # `path:60-64` comments on a range: the note sits on its last line.
    first=
    case "$line" in
      *-*)
        first=${line%-*}
        line=${line#*-}
        ;;
    esac
    path_code=$(printf '%s' "$path" | { sha1sum 2>/dev/null || shasum; } | cut -c1-40)
    read -r base_sha start_sha head_sha <<<"$(printf '%s' "$mr_json" |
      jq -r '.diff_refs | "\(.base_sha) \(.start_sha) \(.head_sha)"')"
    # A renamed file has another name on the base side, and the position names both.
    old_path=$(glab api --paginate "$base/diffs?per_page=100" 2>/dev/null |
      jq -rs --arg p "$path" '[.[] | arrays | .[] | select(.new_path == $p) | .old_path] | first // empty' \
        2>/dev/null || true)
    old_path=${old_path:-$path}
    # A line the diff leaves untouched is addressable only with both sides named,
    # and its number on the old side shifts by whatever the diff added above it.
    created=$(post_inline "$path" "$line" null) ||
      created=$(post_inline "$path" "$line" "${MR_OLD_LINE:-$line}")
    inline=$(printf '%s' "$created" | jq -r '.notes[0].position.new_path // empty')
    if [ -z "$inline" ]; then
      echo "mr-reply: the comment did not land on $path:$line" >&2
      exit 1
    fi
    ;;
  *) created=$(glab api -X POST "$base/discussions/$target/notes" -f body="$body") ;;
esac

# A new thread answers with the discussion; a reply answers with the note alone.
note_id=$(printf '%s' "$created" | jq -r '.notes[0].id // .id')
discussion_id=$(printf '%s' "$created" | jq -r '.id // empty')

if [ -z "$note_id" ] || [ "$note_id" = "null" ]; then
  echo "mr-reply: no note id returned - the watcher would re-surface this thread" >&2
  exit 1
fi
mkdir -p "$(dirname "$notes_file")"
printf '%s %s\n' "$note_id" "$writer_tag" >>"$notes_file"
case "$target" in
  --new | --line) echo "$discussion_id" ;;
  *) echo "$target" ;;
esac
