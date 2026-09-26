import unittest
from datetime import datetime, timezone

from gitstat.common import fmt_hours, parse_when
from gitstat.gitdata import IdentityMap
from gitstat.metrics import compute

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 3, 1, tzinfo=timezone.utc)


def actor(login, typename="User"):
    return {"login": login, "__typename": typename}


def pr(number, author, created, merged=None, merged_by=None, state=None, reviews=(), comments=(),
       inline=(), commits=(), requests=(), size=(10, 5), files=2):
    return {
        "number": number, "title": f"PR {number}", "url": f"https://x/{number}",
        "state": state or ("MERGED" if merged else "OPEN"), "isDraft": False,
        "createdAt": created, "updatedAt": merged or created, "mergedAt": merged, "closedAt": merged,
        "additions": size[0], "deletions": size[1], "changedFiles": files, "baseRefName": "main",
        "author": actor(author), "mergedBy": actor(merged_by) if merged_by else None,
        "reviews": {"nodes": [{"author": actor(w), "state": s, "submittedAt": t} for w, s, t in reviews]},
        "comments": {"nodes": [{"author": actor(w), "createdAt": t} for w, t in comments]},
        "reviewThreads": {"nodes": [{"isResolved": False, "comments": {"nodes": [
            {"author": actor(w), "createdAt": t} for w, t in inline]}}]},
        "commits": {"totalCount": len(commits), "nodes": [{"commit": {
            "oid": f"{number}{i}", "committedDate": t, "authoredDate": t, "additions": 1, "deletions": 0,
            "author": {"name": w, "email": f"{w}@example.com", "user": {"login": w}}}} for i, (w, t) in enumerate(commits)]},
        "timelineItems": {"nodes": [{"__typename": "ReviewRequestedEvent", "createdAt": t,
                                     "requestedReviewer": {"__typename": "User", "login": w}} for w, t in requests]},
    }


def commit(name, email, when, adds=10, dels=2):
    return {"sha": when, "name": name, "email": email, "login": None, "authored": when, "committed": when,
            "additions": adds, "deletions": dels, "files": 1, "subject": "x"}


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.prs = [
            pr(1, "alice", "2026-01-05T10:00:00Z", merged="2026-01-06T10:00:00Z", merged_by="bob",
               requests=[("bob", "2026-01-05T10:00:00Z")],
               reviews=[("bob", "CHANGES_REQUESTED", "2026-01-05T12:00:00Z"),
                        ("bob", "APPROVED", "2026-01-06T09:00:00Z")],
               inline=[("bob", "2026-01-05T12:00:00Z"), ("alice", "2026-01-05T13:00:00Z")],
               commits=[("alice", "2026-01-05T09:00:00Z"), ("alice", "2026-01-05T16:00:00Z")]),
            pr(2, "alice", "2026-01-10T10:00:00Z", merged="2026-01-10T12:00:00Z", merged_by="alice"),
            pr(3, "bob", "2026-01-12T10:00:00Z", reviews=[("dependabot[bot]", "APPROVED", "2026-01-12T11:00:00Z")],
               comments=[("alice", "2026-01-12T14:00:00Z")]),
            pr(4, "carol", "2025-06-01T10:00:00Z", merged="2025-06-02T10:00:00Z"),  # outside window
        ]
        self.commits = [
            commit("Alice A", "alice@example.com", "2026-01-05T09:00:00+00:00"),
            commit("Alice A", "alice@example.com", "2026-01-06T23:30:00+00:00"),
            commit("Bob", "12345+bob@users.noreply.github.com", "2026-01-07T10:00:00+00:00"),
            commit("Robot", "bot@example.com", "2026-01-07T10:00:00+00:00"),
        ]
        self.report = compute(self.prs, self.commits, SINCE, UNTIL, IdentityMap({"bot@example.com": "renovate[bot]"}))
        self.alice = self.report["developers"]["alice"]["metrics"]
        self.bob = self.report["developers"]["bob"]["metrics"]

    def test_authoring(self):
        self.assertEqual(self.alice["prs_opened"], 2)
        self.assertEqual(self.alice["prs_merged"], 2)
        self.assertEqual(self.alice["time_to_merge_median"], 13.0)  # median of 24h and 2h
        self.assertEqual(self.alice["time_to_first_review_median"], 2.0)
        self.assertEqual(self.alice["changes_requested_received"], 1)
        self.assertEqual(self.alice["merged_without_approval"], 1)
        self.assertEqual(self.alice["self_merged"], 1)
        self.assertEqual(self.alice["merge_rate"], 1.0)

    def test_feedback_response(self):
        # Feedback at 12:00, reply at 13:00, next commit at 16:00.
        self.assertEqual(self.alice["feedback_to_reply_median"], 1.0)
        self.assertEqual(self.alice["feedback_to_commit_median"], 4.0)

    def test_reviewing(self):
        self.assertEqual(self.bob["reviews_given"], 2)
        self.assertEqual(self.bob["approvals_given"], 1)
        self.assertEqual(self.bob["changes_requested_given"], 1)
        self.assertEqual(self.bob["review_comments_given"], 1)
        self.assertEqual(self.bob["review_requests"], 1)
        self.assertEqual(self.bob["review_response_rate"], 1.0)
        self.assertEqual(self.bob["review_turnaround_median"], 2.0)
        self.assertEqual(self.bob["merges_for_others"], 1)
        self.assertEqual(self.alice["pr_comments_given"], 1)

    def test_commits_and_identity(self):
        self.assertEqual(self.alice["commits"], 2)  # email learned from PR commits
        self.assertEqual(self.alice["active_days"], 2)
        self.assertEqual(self.alice["longest_streak_days"], 2)
        self.assertEqual(self.alice["off_hours_pct"], 0.5)
        self.assertEqual(self.bob["commits"], 1)  # noreply address

    def test_bots_and_window(self):
        devs = self.report["developers"]
        self.assertNotIn("dependabot[bot]", devs)
        self.assertNotIn("renovate[bot]", devs)
        self.assertNotIn("carol", devs)

    def test_exclude(self):
        report = compute(self.prs, self.commits, SINCE, UNTIL, IdentityMap(), exclude={"bob"})
        self.assertNotIn("bob", report["developers"])


class HelpersTest(unittest.TestCase):
    def test_parse_when(self):
        ref = datetime(2026, 3, 1, tzinfo=timezone.utc)
        self.assertEqual(parse_when("30d", ref), datetime(2026, 1, 30, tzinfo=timezone.utc))
        self.assertEqual(parse_when("2026-01-01"), SINCE)

    def test_fmt_hours(self):
        self.assertEqual(fmt_hours(0.5), "30m")
        self.assertEqual(fmt_hours(5), "5.0h")
        self.assertEqual(fmt_hours(72), "3.0d")
        self.assertEqual(fmt_hours(None), "–")


if __name__ == "__main__":
    unittest.main()
