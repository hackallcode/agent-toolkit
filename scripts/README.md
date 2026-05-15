# scripts

Helper scripts that support executing the agent instructions in `modes/**`.
Prefer these over ad-hoc one-liners so behaviour stays consistent and reviewed.

The instructions themselves name no forge; these scripts are their GitLab
implementation, talking to it through the `glab` CLI. A project on another forge
carries its own set behind the same roles - a watch per role and one way to write
a note - and the instructions stay as they are.

## `mr-author-watch.py` and `mr-review-watch.py`

Watches a GitLab MR for review activity; for the BUILD-mode
**MR-review-monitor** (see `modes/build.md`). Run under the `Monitor` tool
(persistent) - each emitted record is one event, process exit ends the watch.
`--record-separator` frames records with NUL for wrappers that must preserve
multi-line comments.

```text
scripts/mr-author-watch.py --mr <iid> [--project group/repo] [--interval 15] [--record-separator]
scripts/mr-review-watch.py --mr <iid> [--project group/repo] [--interval 15] [--record-separator]
```

- One script per watch, because the two report different things and an agent
  should not have to pick a flag to get the right stream. The author acts on CI,
  approvals and the ticks in the test plan; the reviewer acts on new code and gets
  `NEW COMMIT <sha>` when the branch moves.
  The author's test-plan thread is dropped for that role too, until we write in it
  ourselves and their answer is addressed to us.

- Polls discussions on the **MR diff** + on **each commit's diff**, fully
  paginated (per_page=100 across all pages - not just the first 100).
- Emits comments in any **unresolved** thread that is AWAITING the agent - i.e.
  whose latest note isn't the agent's own reply (opening comment + reviewer
  follow-ups), plus commit-diff comments. Once the agent replies, the thread goes
  quiet until the reviewer adds more or the author resolves it. So a still-open
  thread is **re-surfaced after a monitor restart** (the seen-set starts empty),
  not lost. A resolved thread is skipped but not written off, so re-opening it
  surfaces its comments again.
- A comment on a line arrives with the piece of diff it was written on: what its
  selection covers marked `→`, a few lines around it, both sides' numbers, in the
  `+`/`-` shape GitLab shows. Ends on one side (`+65..+68`) mean that side's lines
  and nothing else; ends on different sides (`-53..+66`) mean the run between them,
  which only the diff can say. And the diff is the one the note names
  (`base_sha` → `head_sha`), not the MR's today: the same numbers in a later diff
  point at other lines.
- Emits `APPROVED by <user>` on each not-approved → approved transition (the
  GitLab «Approve» button), the trigger for the on-approval cleanup + check pass.
  Re-approval after a reset (e.g. a new push clears approvals) re-emits.
- Emits `CI <sha> <status>` when the pipeline of the MR head lands - the gate for
  running our own checks off the test plan - naming the failed jobs and the lines
  of their logs that name the failure. Only the newest pipeline is reported, so a
  push that cancels the previous one costs no event; each pipeline is reported
  once, and a restart reports the current one, since that is the state the agent
  needs. No hand-rolled polling loop: a `sleep`-and-`glab` one-liner ends up
  waiting on a pipeline a later push already cancelled.
- Emits `TEST PLAN …` lines for the checklist of the test-plan comment
  (`modes/build.md` → «Тест-план в треде MR»): an item ticked or un-ticked, the
  list complete, and a tick whose commit is no longer the MR head. The list is
  edited in place, so these come from comparing note bodies between polls; the
  first poll only takes a baseline, but an already-complete list is announced
  once per run, because that is the cue to resolve the thread. A tick naming a
  commit is the agent's own bookkeeping and is not read back to it; the bare tick
  a human clicked is the one that becomes an event.
- Don't resolve threads to silence them - the author does that. The one exception
  is our own test-plan thread, resolved once every box is ticked and the MR is
  approved.
- Self-loop avoidance (single account): the notes file
  (`~/.agent-toolkit/mr-agent-notes`, override with `MR_AGENT_NOTES`) holds one
  `<note id> <writer tag>` record per note `mr-reply.sh` posted, and a note is
  ours when it is recorded under this watcher's tag **or** signed with its
  marker - the `<!-- agent-toolkit:<id> -->` line the script appends, whose
  `<id>` names the agent (host and writer tag), so a watcher on another
  machine, which holds no record, still tells our notes. Only the closing line
  signs: review tooling quotes a marker back, and the reviewer's comment must
  not be taken for an answer and dropped; a quoted marker, another agent's
  marker and the bare `<!-- agent -->` of older notes all stay unread. A thread
  counts as "answered" while its latest note is one of those.
- The tag defaults to the working-tree root (`MR_AGENT_TAG` overrides it),
  which both scripts derive the same way; the watcher prints the tag and the
  marker it uses on stderr at startup. The tag is what keeps the file usable by
  several agents at once: every agent on the account appends to it, and an id
  carrying another tag - or no tag, as other tooling writes it - is someone
  else's comment and is still reported. Set `MR_AGENT_TAG` in both processes
  when the monitor and the replies cannot share a working directory.
- On merge/close it emits a final `MERGED`/`CLOSED` line and exits, stopping the
  monitor.
- `--project` defaults to the `origin` remote; `--interval` defaults to 15s.
- Events are records, not lines: a multi-line comment or a CI failure with its
  log lines is one record. Newline-separated by default; `--record-separator`
  frames records with NUL.
- Auth/transport go through the `glab` CLI - no token handling in the script.

## `mr-reply.sh`

Seven modes of one thing - putting a note in an MR and keeping the one thread we
open ourselves: a reply in a thread, a thread of our own (`--new`), a review
comment on a line of the diff (`--line path:line`), `--resolve <id>`,
`--unresolve <id>`, and `--show <id>` and `--edit <id>` of the note at its head.

Writes into an MR thread - a reply, or `--new` to open one, e.g. the test plan
(`modes/build.md`) - and records the note as `<note id> <writer tag>` in the
notes file, which is how the watches tell our own notes from the reviewer's
comments. Post through this script - anything posted straight through `glab`
leaves no record behind, so the watcher keeps re-surfacing that thread after every
restart.

```text
scripts/mr-reply.sh <mr-iid> <discussion-id> [body-file]
scripts/mr-reply.sh <mr-iid> --new [body-file]
scripts/mr-reply.sh <mr-iid> --line <path>:<line> [body-file]
scripts/mr-reply.sh <mr-iid> --line <path>:<from>-<to> [body-file]
scripts/mr-reply.sh <mr-iid> --resolve <discussion-id>
scripts/mr-reply.sh <mr-iid> --unresolve <discussion-id>
scripts/mr-reply.sh <mr-iid> --show <discussion-id>
scripts/mr-reply.sh <mr-iid> --edit <discussion-id> [body-file]
echo 'text' | scripts/mr-reply.sh <mr-iid> <discussion-id>
```

- Body comes from a file or stdin, so markdown and quotes need no shell escaping.
- Prints the discussion id, which is what a later reply into the same thread
  needs; `--edit` prints the note it rewrote and `--show` the body it read.
  Fails loudly when GitLab returns no note id, since an unrecorded note is the
  case the watcher cannot recover from.
- `MR_PROJECT` overrides the project, which otherwise comes from the `origin`
  remote, as it does for the watches; `MR_AGENT_NOTES` overrides the notes file and
  `MR_AGENT_TAG` the writer tag - the last two must match what the watcher uses.
- `--line` takes the base-side name of a renamed file from the MR diff, and
  `<path>:<from>-<to>` comments on a range of lines, anchored on the last one.
- A note is held to 1500 characters (`MR_MAX_NOTE_LEN`): a longer one goes only
  with the reason named in `MR_ALLOW_LONG`, and never past 6000
  (`MR_MAX_NOTE_LEN_HARD`). The head note of a test plan is a checklist and is
  not counted.
- The agent's marker `<!-- agent-toolkit:<id> -->` is appended by the script
  unless the body already ends with it: it signs the reply for the reader, and
  for the watches of an agent whose record file has no note of ours.
- The rules of a thread are refused, not just written down. A reply carrying
  `- [ ]` or `- [x]` is turned away, because boxes are counted in the head note
  alone. A long dash is turned away. `--resolve` closes a thread only when every
  box is ticked, every automatic tick names a run (`- [x] <проверка> - <sha>`,
  read off the tail) and the head note is recorded under **this** writer tag -
  somebody else's thread is resolved by its author.
- `--show` prints the body of the head note: the plan is read back from the
  forge before it is rewritten, or a snapshot of ours drops the ticks a human
  put in. It is the one mode that asks nothing about ownership.
- `--edit` writes a body over that same head note, boxes and all, and turns away
  an empty one, which would take the plan with it; a body file that is named and
  not there is an error, never a body read off stdin. `--unresolve` puts a thread
  back to open and counts no boxes, a new check being why it is re-opened. Both
  name the thread, not the note, and both want its head note recorded under this
  writer tag - the record gone (another `MR_AGENT_TAG`, a working tree moved),
  the line `<note id> <writer tag>` goes into the file by hand.
- The title and the description of the MR are ours to keep English: while they
  are not, no note goes out. Reviewing somebody's MR (`MR_AGENT_TAG` starting
  with `review-`) the note still goes, with the same line as a warning - we do
  not edit their title, and the comment asking for it has to reach them.
- The test plan has two groups, `Автоматические проверки` and `Ручные
  проверки`; a group is read by its first word, with or without markdown around
  it. A tick under `Ручные проверки` is the human's and needs no sha.
- `jq`, `awk` and `python3` are what reads the answers, the plan and the marker,
  so all three have to be on the path; `tests/test_mr_reply_guards.py` and
  `tests/test_threads_marker.py` cover what the script refuses and what counts
  as a signature.

## `mr_watch/`

Both watches are thin CLIs; what they poll for lives in the package beside them,
one owner per file, and `loop.py` holds the part they share. The package is imported co-located (its directory
goes on the path, because the script itself carries a dash), and a test loads the
owner it is about rather than the CLI in front of it:

| Module         | Owns                                                            |
| -------------- | --------------------------------------------------------------- |
| `threads.py`   | whose note a thread waits on: the record, the marker, the tracker |
| `test_plan.py` | the test-plan checklist and what changed between two versions   |
| `pipelines.py` | the outcome of the newest pipeline, the failure lines, the head |
| `render.py`    | how a comment and the lines it covers reach the event stream    |
| `glab.py`      | `glab api` calls, pagination, the project from the remote       |

HTTP 429 stops either watcher immediately with exit status 1 and a concise stderr error. Pagination,
commit discussions, diff rendering, pipeline logs and approvals propagate that failure: no later
endpoint or polling tick is requested. The watcher does not retry a 429, sleep until an assumed
expiry, or restart itself. There is no shared cooldown; restarting requires the operator to establish
that requests are safe.

A network/timeout read keeps the bounded two-attempt policy: the first attempt waits up to 60s and
the second up to 20s. Exhaustion stops the watcher with exit status 1, without another endpoint or
tick. A nonzero `glab` exit without an HTTP status is treated as a transport failure, without relying
on an exhaustive list of network error messages. Fatal errors never quote request URLs, response
headers, bodies or transport diagnostics.

Other non-429 HTTP errors and unreadable JSON retain their existing behaviour: named 4xx errors
are not retried; server errors and malformed JSON get the second bounded attempt. Unreadable
answers are reported on stderr, and pagination returns the items read before a lost page. Reading
replaces undecodable UTF-8 bytes rather than raising. stdout remains the event stream.
