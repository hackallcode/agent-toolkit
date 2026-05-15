#!/usr/bin/env python3
"""Watch an MR we opened: what review says, what CI says, and what the human ticked.

Built for the BUILD-mode MR-review-monitor (`modes/build.md`): run it under the
harness `Monitor` tool - each emitted record becomes one event, and process exit
ends the watch. `--record-separator` frames records with NUL for wrappers that
must preserve multi-line comments. The reviewer's side of the same MR is
`mr-review-watch.py`.

What it emits:
- Comments in any **unresolved** discussion that is AWAITING us - i.e. whose
  latest note is not our own reply - and the same for comments on the MR's
  commits' diffs.
- `CI <sha> <status>` when the pipeline of the head lands, with the failed jobs
  named and the lines of their logs that say what failed. Only the newest
  pipeline is watched: a push abandons the one that was running, and its
  cancellation is not news to whoever pushed.
- `TEST PLAN` lines for the checklist of the test-plan comment: a box ticked by
  the human, a box cleared, the list gone complete (the cue to resolve the thread
  once the MR is approved), and a tick whose commit the branch has moved past.
  Our own ticks name a commit and are not read back to us.
- `APPROVED by <user>` on each not-approved → approved transition, the trigger
  for the on-approval cleanup + check pass. A re-approval after a reset re-emits.
- `MERGED`/`CLOSED` when the MR leaves the opened state - then the process exits.

Usage:
    mr-author-watch.py --mr 55 [--project group/repo] [--interval 15]
        [--record-separator]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# The package beside this script holds what it polls for; the directory goes on
# the path because the script itself carries a dash and is not importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_watch.glab import WatchReadError, api, paginated, raw  # noqa: E402
from mr_watch.loop import Watch  # noqa: E402
from mr_watch.pipelines import PipelineTracker, failure_lines  # noqa: E402
from mr_watch.test_plan import TestPlanTracker, is_test_plan  # noqa: E402


def main() -> int:
    watch = Watch(__doc__ or "")
    plans: dict[str, TestPlanTracker] = {}
    pipelines = PipelineTracker()
    approvers: set[str] = set()

    while True:
        state, head_sha, info = watch.state()
        watch.report_comments()

        # The checklist lives in the head note and is edited in place, so its
        # ticks are state to compare, not new notes to hand out.
        for head, _notes, _label in watch.threads():
            if not is_test_plan(head.get("body", "")):
                continue
            plan = plans.setdefault(str(head.get("id")), TestPlanTracker())
            for event in plan.update(head.get("body", ""), head_sha):
                watch.say(event)

        landed = pipelines.landed(paginated(f"{watch.base}/pipelines"))
        if landed is not None:
            report_pipeline(watch, landed)

        approvals = api(f"{watch.base}/approvals")
        if isinstance(approvals, dict):
            current = {
                (entry.get("user") or {}).get("username")
                for entry in approvals.get("approved_by") or []
            }
            current.discard(None)
            for name in current - approvers:
                watch.say(f"APPROVED by {name}")
            # A failed poll leaves the set alone, so no spurious re-emit follows.
            approvers = current

        if watch.closed(state, info):
            return 0
        time.sleep(watch.interval)


def report_pipeline(watch: Watch, landed: dict) -> None:
    sha = (landed.get("sha") or "")[:8]
    if landed.get("status") == "success":
        watch.say(f"CI {sha} success")
        return
    failed = [
        job
        for job in paginated(f"projects/{watch.project}/pipelines/{landed['id']}/jobs")
        if job.get("status") == "failed"
    ]
    names = ", ".join(job.get("name", "?") for job in failed) or "no failed job"
    lines = [f"CI {sha} {landed.get('status')}: {names}"]
    for job in failed:
        log = raw(f"projects/{watch.project}/jobs/{job['id']}/trace")
        lines.extend(f"    {job.get('name', '?')}: {line}" for line in failure_lines(log))
    watch.say("\n".join(lines))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except WatchReadError as exc:
        print(f"mr-watch: {exc}", file=sys.stderr)
        sys.exit(1)
