"""Tests for the bulk token replacement helper (pure functions only — the
Graph API call and SSH are exercised by the operator, not by unit tests)."""

import unittest

from audit_snapshot.replace_account_tokens import (
    _apply_plan,
    _build_plan,
    _check_stored,
    _check_untouched,
    _decode_accounts,
    _extract_value,
    _mask,
    _replace_value,
)


def _accounts():
    return [
        {"name": "small", "user_id": "1", "access_token": "OLD1"},
        {"name": "keep", "user_id": "2", "access_token": "KEEP"},
    ]


class ReplaceAccountTokensTests(unittest.TestCase):
    def test_single_quoted_value_is_parsed_and_quote_kept(self):
        raw = "BOT_TOKEN=x\nINSTAGRAM_ACCOUNTS='[{\"name\":\"a\"}]'\nOTHER=1\n"
        value, quote = _extract_value(raw)
        self.assertEqual(value, '[{"name":"a"}]')
        self.assertEqual(quote, "'")
        self.assertEqual(_decode_accounts(raw), [{"name": "a"}])

    def test_unquoted_value_is_parsed(self):
        raw = "INSTAGRAM_ACCOUNTS=[{\"name\":\"a\"}]\n"
        value, quote = _extract_value(raw)
        self.assertEqual(value, '[{"name":"a"}]')
        self.assertEqual(quote, "")

    def test_replace_keeps_other_lines_and_crlf(self):
        raw = (
            "BOT_TOKEN=x\r\n"
            "INSTAGRAM_ACCOUNTS='[{\"name\":\"a\",\"access_token\":\"OLD\"}]'\r\n"
            "OTHER=1\r\n"
        )
        new_value = '[{"name":"a","access_token":"NEW"}]'
        result = _replace_value(raw, new_value, "'")
        self.assertEqual(
            result,
            "BOT_TOKEN=x\r\n"
            f"INSTAGRAM_ACCOUNTS='{new_value}'\r\n"
            "OTHER=1\r\n",
        )

    def test_plan_marks_only_changed_tokens(self):
        tokens = {"small": "NEW1", "keep": "KEEP"}
        plan = _build_plan(_accounts(), tokens, verify=False)
        self.assertEqual([entry["changed"] for entry in plan], [True, False])

    def test_plan_rejects_unknown_account(self):
        with self.assertRaises(SystemExit):
            _build_plan(_accounts(), {"typo.account": "X"}, verify=False)

    def test_apply_plan_touches_only_planned_accounts(self):
        plan = _build_plan(_accounts(), {"small": "NEW1"}, verify=False)
        updated = _apply_plan(_accounts(), plan)
        self.assertEqual(updated[0]["access_token"], "NEW1")
        self.assertEqual(updated[1], _accounts()[1])
        _check_untouched(_accounts(), updated, plan)

    def test_check_untouched_detects_drift(self):
        plan = _build_plan(_accounts(), {"small": "NEW1"}, verify=False)
        drifted = [dict(_accounts()[0]), {"name": "keep", "user_id": "2", "access_token": "OOPS"}]
        with self.assertRaises(SystemExit):
            _check_untouched(_accounts(), drifted, plan)

    def test_check_stored_detects_wrong_token(self):
        plan = _build_plan(_accounts(), {"small": "NEW1"}, verify=False)
        with self.assertRaises(SystemExit):
            _check_stored(plan, _accounts())
        _check_stored(plan, [{"name": "small", "access_token": "NEW1"}])

    def test_mask_never_reveals_the_middle(self):
        token = "IGAAhABiE4S8SECRETMIDDLE12345"
        masked = _mask(token)
        self.assertNotIn("SECRETMIDDLE", masked)
        self.assertTrue(masked.startswith("IGAAhA"))
        self.assertIn(str(len(token)), masked)


if __name__ == "__main__":
    unittest.main()
