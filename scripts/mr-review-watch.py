#!/usr/bin/env python3
"""Watch an MR we review: what the author answered, and what new code arrived.

Run it under the harness `Monitor` tool for the review subagent of that MR
(`modes/review.md`); the author's side of the same MR is `mr-author-watch.py`.

What it emits (one line per event):
- Comments in any **unresolved** discussion that is AWAITING us - the author's
  answers in our threads, their questions, and comments on the commits' diffs.
  The author's own test-plan thread stays out until we write in it ourselves and
  their answer is addressed to us.
- `NEW COMMIT <sha>` when the branch moves, which is the code to read. CI,
  approvals and the ticks in the test plan belong to the author's watch.
- `MERGED`/`CLOSED` when the MR leaves the opened state - then the process exits,
  and only then is the review subagent of this MR done with.

Usage:
    mr-review-watch.py --mr 55 [--project group/repo] [--interval 15]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# The package beside this script holds what it polls for; the directory goes on
# the path because the script itself carries a dash and is not importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_watch.glab import WatchReadError  # noqa: E402
from mr_watch.loop import Watch  # noqa: E402
from mr_watch.pipelines import HeadTracker  # noqa: E402
from mr_watch.test_plan import authors_bookkeeping  # noqa: E402


def main() -> int:
    watch = Watch(__doc__ or "")
    commits = HeadTracker()

    while True:
        state, head_sha, info = watch.state()
        watch.report_comments(
            skip=lambda head, notes: authors_bookkeeping(
                head.get("body", ""), notes, watch.tracker.own_ids(notes)
            )
        )

        moved = commits.moved(head_sha)
        if moved:
            watch.say(f"NEW COMMIT {moved[:8]}")

        if watch.closed(state, info):
            return 0
        time.sleep(watch.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except WatchReadError as exc:
        print(f"mr-watch: {exc}", file=sys.stderr)
        sys.exit(1)
