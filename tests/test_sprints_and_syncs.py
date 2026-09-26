import unittest
from datetime import datetime, timedelta, timezone

from gitstat.gitdata import IdentityMap
from gitstat.metrics import compute, is_base_sync
from gitstat.periods import parse_length, sprints, weekly

from test_metrics import pr

UTC = timezone.utc


class PeriodsTest(unittest.TestCase):
    def test_sprints_extend_backwards_from_future_anchor(self):
        anchor = datetime(2026, 9, 29, tzinfo=UTC)  # next sprint starts on a Tuesday
        p = sprints(anchor, 14, datetime(2026, 8, 20, tzinfo=UTC), datetime(2026, 9, 26, tzinfo=UTC))
        self.assertEqual([s.date().isoformat() for s in p.starts], ["2026-08-18", "2026-09-01", "2026-09-15"])
        self.assertTrue(all(s.weekday() == 1 for s in p.starts))
        self.assertTrue(p.to_json()[-1]["in_progress"])
        self.assertEqual(p.index(datetime(2026, 9, 14, 23, tzinfo=UTC)), 1)
        self.assertEqual(p.index(datetime(2026, 9, 15, tzinfo=UTC)), 2)

    def test_sprints_forward_from_past_anchor(self):
        p = sprints(datetime(2026, 1, 6, tzinfo=UTC), 14, datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 3, 20, tzinfo=UTC))
        self.assertEqual(p.starts[0].date().isoformat(), "2026-02-17")

    def test_weekly_and_lengths(self):
        p = weekly(datetime(2026, 9, 2, tzinfo=UTC), datetime(2026, 9, 20, tzinfo=UTC))
        self.assertEqual(p.starts[0].date().isoformat(), "2026-08-31")
        self.assertEqual((parse_length("2w"), parse_length("10d"), parse_length("14")), (14, 10, 14))


class BaseSyncTest(unittest.TestCase):
    def test_detects_merges_from_target_branch(self):
        self.assertTrue(is_base_sync("Merge branch 'develop' into feature/x", 2, "develop"))
        self.assertTrue(is_base_sync("Merge remote-tracking branch 'origin/develop' into feature/x", 2, "develop"))
        self.assertTrue(is_base_sync("Merge branch 'main' of github.com:org/repo into fix", 2, "release"))
        self.assertTrue(is_base_sync("Merge branch 'release/2.0' into hotfix", 2, "release/2.0"))
        self.assertTrue(is_base_sync("Merge remote-tracking branch 'origin/main'", 2, "main"))

    def test_ignores_other_merges_and_normal_commits(self):
        self.assertFalse(is_base_sync("Merge branch 'feature/y' into feature/x", 2, "develop"))
        self.assertFalse(is_base_sync("Merge branch 'feat/p2-settings-ui'", 2, "main"))
        self.assertFalse(is_base_sync("Merge branch 'develop' into feature/x", 1, "develop"))
        self.assertFalse(is_base_sync("fix: handle develop branch", 2, "develop"))


def with_commit_meta(p, meta):
    for node, (headline, parents) in zip(p["commits"]["nodes"], meta):
        node["commit"]["messageHeadline"] = headline
        node["commit"]["parents"] = {"totalCount": parents}
    return p


class ComputeTest(unittest.TestCase):
    def setUp(self):
        since, until = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)
        a = with_commit_meta(pr(1, "alice", "2026-01-05T10:00:00Z", merged="2026-01-09T10:00:00Z",
                                reviews=[("bob", "COMMENTED", "2026-01-06T10:00:00Z")],
                                commits=[("alice", "2026-01-05T09:00:00Z"), ("alice", "2026-01-07T09:00:00Z"),
                                         ("alice", "2026-01-08T09:00:00Z")]),
                             [("feat: x", 1), ("Merge branch 'develop' into feat-x", 2), ("fix: review", 1)])
        a["baseRefName"] = "develop"
        a["timelineItems"]["nodes"].append({"__typename": "HeadRefForcePushedEvent", "createdAt": "2026-01-08T12:00:00Z",
                                            "actor": {"__typename": "User", "login": "alice"}})
        b = pr(2, "alice", "2026-01-10T10:00:00Z", comments=[("bob", "2026-01-11T10:00:00Z")])
        c = pr(3, "carol", "2026-01-12T10:00:00Z", reviews=[("bob", "APPROVED", "2026-01-12T12:00:00Z")])
        self.report = compute([a, b, c], [], since, until, IdentityMap(),
                              periods=sprints(datetime(2026, 1, 6, tzinfo=UTC), 14, since, until))

    def test_branch_updates(self):
        m = self.report["developers"]["alice"]["metrics"]
        self.assertEqual(m["base_merges"], 1)
        self.assertEqual(m["force_pushes"], 1)
        self.assertEqual(m["prs_kept_current"], 1)
        self.assertEqual(m["pr_commits"], 2)  # the sync merge isn't counted as work
        # Feedback at 01-06 10:00 -> next real commit 01-08 09:00 (the 01-07 merge doesn't count).
        self.assertEqual(m["feedback_to_commit_median"], 47.0)

    def test_review_coverage_for_percentages(self):
        devs = self.report["developers"]
        self.assertEqual(devs["alice"]["prs_open_in_window"], 2)
        self.assertEqual(devs["bob"]["review_prs"], {"alice": 2, "carol": 1})

    def test_series_follow_sprints(self):
        r = self.report
        self.assertEqual(r["period"], {"kind": "sprint", "days": 14})
        self.assertEqual(r["periods"][0]["start"][:10], "2025-12-23")
        self.assertEqual(r["developers"]["alice"]["series"]["prs_opened"][:2], [1, 1])


if __name__ == "__main__":
    unittest.main()
