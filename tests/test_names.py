import unittest

from gitstat.names import apply_names, build_name_map, find_developer


def report():
    return {"developers": {
        "a123456": {"login": "a123456", "interactions": {"b654321": 3}},
        "b654321": {"login": "b654321", "interactions": {}},
        "octocat": {"login": "octocat", "interactions": {"a123456": 1}},
    }}


class NamesTest(unittest.TestCase):
    def test_config_beats_profile_and_is_case_insensitive(self):
        m = build_name_map(["a123456", "b654321"], {"A123456": "Jane Doe"}, {"a123456": "J. D.", "b654321": "Bob Roe"})
        self.assertEqual(m, {"a123456": "Jane Doe", "b654321": "Bob Roe"})

    def test_duplicate_names_get_login_suffix(self):
        m = build_name_map(["a1", "a2"], {"a1": "Sam Lee", "a2": "Sam Lee"})
        self.assertEqual(m, {"a1": "Sam Lee", "a2": "Sam Lee (a2)"})

    def test_apply_renames_keys_and_interactions(self):
        r = report()
        apply_names(r, {"a123456": "Jane Doe", "b654321": "Bob Roe"})
        self.assertEqual(list(r["developers"]), ["Jane Doe", "Bob Roe", "octocat"])
        jane = r["developers"]["Jane Doe"]
        self.assertEqual(jane["github_login"], "a123456")
        self.assertEqual(jane["interactions"], {"Bob Roe": 3})
        self.assertEqual(r["developers"]["octocat"]["interactions"], {"Jane Doe": 1})

    def test_find_by_login_or_name(self):
        r = report()
        apply_names(r, {"a123456": "Jane Doe"})
        self.assertEqual(find_developer(r, "jane doe"), "Jane Doe")
        self.assertEqual(find_developer(r, "@A123456"), "Jane Doe")
        self.assertEqual(find_developer(r, "octocat"), "octocat")
        self.assertIsNone(find_developer(r, "nobody"))


if __name__ == "__main__":
    unittest.main()


class CollisionTest(unittest.TestCase):
    def test_name_cannot_overwrite_unrenamed_key(self):
        # An unmatched commit author keyed "Vyapak Goyal" must survive a login whose profile name is the same.
        m = build_name_map(["gts-47", "Vyapak Goyal"], {}, {"gts-47": "Vyapak Goyal"})
        self.assertEqual(m, {"gts-47": "Vyapak Goyal (gts-47)"})

    def test_match_by_profile_name_requires_unique_match(self):
        from gitstat.names import match_by_profile_name
        profile = {"gts-47": "Vyapak Goyal", "a1": "Sam Lee", "a2": "Sam Lee", "Gulum": "Julian"}
        found = match_by_profile_name(["Vyapak Goyal", "Sam Lee", "Julian", "Nobody"], profile)
        self.assertEqual(found, {"Vyapak Goyal": "gts-47"})  # ambiguous and single-word names are left alone
