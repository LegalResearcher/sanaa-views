import json
import os
import tempfile
import unittest
from unittest.mock import patch
from main import (
    discover_new_links,
    load_previous_batch,
    pending_links,
    prepare_run_batch,
    run_browser_send,
)
import main
from processing_state import (
    clear_processing_state_if_paused,
    is_automation_paused,
    load_account_history,
    pause_automation,
)
from retry_link import process_manual_retry


class FakeSender:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def __call__(self, link):
        self.calls.append(link)
        return self.outcomes.pop(0) if self.outcomes else "ok"


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = self.temp.name
        self.links = [f"https://t.me/SanaaPressAgency/{number}" for number in range(1994, 2001)]
        self.sleep_patch = patch("main.time.sleep", return_value=None)
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        self.temp.cleanup()

    def test_new_links_precede_previous_batch_and_are_deduplicated(self):
        with open(os.path.join(self.state, "previous_batch.json"), "w", encoding="utf-8") as file:
            json.dump([self.links[0], self.links[1]], file)
        batch = prepare_run_batch([self.links[2], self.links[0]], self.state)
        self.assertEqual(batch, [self.links[2], self.links[0], self.links[1]])
        self.assertEqual(load_previous_batch(self.state), batch)

    def test_legacy_queue_migration_keeps_all_incomplete_links(self):
        with open(os.path.join(self.state, "telegram_links.txt"), "w", encoding="utf-8") as file:
            file.write("\n".join(self.links) + "\n")
        with open(os.path.join(self.state, "success_counts.json"), "w", encoding="utf-8") as file:
            json.dump({self.links[0]: 9}, file)
        migrated = load_previous_batch(self.state)
        self.assertEqual(migrated, self.links[1:])

    def test_only_new_links_are_discovered_once(self):
        found = discover_new_links(self.state, [self.links[0], self.links[1]])
        self.assertEqual(found, self.links[:2])
        self.assertEqual(discover_new_links(self.state, [self.links[0], self.links[1], self.links[2]]), [self.links[2]])

    def test_start_after_post_id_excludes_anchor_and_older_links(self):
        before = "https://t.me/SanaaPressAgency/1992"
        anchor = "https://t.me/SanaaPressAgency/1993"
        after = "https://t.me/SanaaPressAgency/1994"
        self.assertEqual(discover_new_links(self.state, [before, anchor, after]), [after])

        with open(os.path.join(self.state, "previous_batch.json"), "w", encoding="utf-8") as file:
            json.dump([before, anchor, after], file)
        self.assertEqual(prepare_run_batch([], self.state), [after])

    def test_account_is_not_given_same_link_twice_but_next_account_can_process_it(self):
        batch = self.links[:2]
        first = FakeSender()
        self.assertEqual(run_browser_send(first, self.state, 1, batch), 0)
        self.assertEqual(first.calls, batch)
        repeated = FakeSender()
        self.assertEqual(run_browser_send(repeated, self.state, 1, batch), 0)
        self.assertEqual(repeated.calls, [])
        next_account = FakeSender()
        self.assertEqual(run_browser_send(next_account, self.state, 2, batch), 0)
        self.assertEqual(next_account.calls, batch)
        self.assertEqual(load_account_history(self.state), {link: [1, 2] for link in batch})

    def test_failed_attempt_is_still_recorded_for_that_account(self):
        sender = FakeSender(["fail"])
        run_browser_send(sender, self.state, 3, [self.links[0]])
        self.assertEqual(pending_links([self.links[0]], 3, self.state), [])
        self.assertEqual(pending_links([self.links[0]], 4, self.state), [self.links[0]])

    def test_scheduled_run_skips_links_at_success_or_failure_limit(self):
        with open(os.path.join(self.state, "success_counts.json"), "w", encoding="utf-8") as file:
            json.dump({self.links[0]: 9}, file)
        with open(os.path.join(self.state, "failed_attempts.json"), "w", encoding="utf-8") as file:
            json.dump({self.links[1]: 3}, file)

        sender = FakeSender()
        run_browser_send(sender, self.state, 1, self.links[:3])

        self.assertEqual(sender.calls, [self.links[2]])

    def test_run_processes_more_than_old_limit(self):
        batch = [f"https://t.me/SanaaPressAgency/{number}" for number in range(1, 12)]
        sender = FakeSender()
        run_browser_send(sender, self.state, 1, batch)
        self.assertEqual(sender.calls, batch)
        self.assertEqual(load_previous_batch(self.state), batch)

    def test_completed_and_exhausted_links_are_removed_from_saved_batch(self):
        with open(os.path.join(self.state, "done_links.txt"), "w", encoding="utf-8") as file:
            file.write(self.links[0] + "\n")
        with open(os.path.join(self.state, "failed_attempts.json"), "w", encoding="utf-8") as file:
            json.dump({self.links[1]: 3}, file)
        run_browser_send(FakeSender(), self.state, 1, self.links[:3])
        self.assertEqual(load_previous_batch(self.state), [self.links[2]])

    def test_main_skips_login_when_no_account_has_eligible_links(self):
        with open(os.path.join(self.state, "done_links.txt"), "w", encoding="utf-8") as file:
            file.write(self.links[0] + "\n")
        with patch.object(main, "STATE_DIR", self.state), \
             patch.object(main, "discover_new_links", return_value=[self.links[0]]), \
             patch.object(main, "select_account", side_effect=AssertionError("login selection called")):
            self.assertEqual(main.main(), 0)

    def test_main_selects_next_account_with_eligible_link(self):
        from account_rotation import select_account
        with open(os.path.join(self.state, "link_account_history.json"), "w", encoding="utf-8") as file:
            json.dump({self.links[0]: [1]}, file)
        selected = select_account(self.state, {f"SITE_USERNAME_{n}": f"u{n}" for n in range(1, 10)} | {f"SITE_PASSWORD_{n}": f"p{n}" for n in range(1, 10)}, eligible=lambda n: n != 1)
        self.assertEqual(selected[0], 2)
        with open(os.path.join(self.state, "account_rotation.json"), encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"next_account": 3})

    def test_manual_retry_is_blocked_while_automation_is_paused(self):
        pause_automation(self.state, "test pause")
        sender = FakeSender()
        self.assertEqual(process_manual_retry(self.links[0], sender, self.state), 1)
        self.assertEqual(sender.calls, [])

    def test_normal_link_failure_does_not_pause_automation(self):
        sender = FakeSender(["fail"])
        self.assertEqual(process_manual_retry(self.links[0], sender, self.state), 1)
        self.assertEqual(sender.calls, [self.links[0]])
        self.assertFalse(is_automation_paused(self.state))

    def test_cleanup_requires_fatal_pause_and_preserves_pause_marker_and_gitkeep(self):
        state_files = (
            "account_rotation.json", "failed_attempts.json", "link_account_history.json",
            "previous_batch.json", "processed_links.txt", "success_counts.json",
            "telegram_links.txt",
        )
        for filename in state_files:
            with open(os.path.join(self.state, filename), "w", encoding="utf-8") as file:
                file.write("sentinel")
        gitkeep = os.path.join(self.state, ".gitkeep")
        open(gitkeep, "w", encoding="utf-8").close()

        self.assertFalse(clear_processing_state_if_paused(self.state))
        self.assertTrue(all(os.path.exists(os.path.join(self.state, name)) for name in state_files))

        pause_automation(self.state, "fatal test")
        self.assertTrue(clear_processing_state_if_paused(self.state))
        self.assertTrue(is_automation_paused(self.state))
        self.assertTrue(os.path.exists(gitkeep))
        self.assertFalse(any(os.path.exists(os.path.join(self.state, name)) for name in state_files))

    def test_fatal_result_does_not_retry_link_on_same_account(self):
        sender = FakeSender(["fatal"])
        self.assertEqual(run_browser_send(sender, self.state, 5, [self.links[0], self.links[1]]), 1)
        self.assertEqual(sender.calls, [self.links[0]])
        self.assertTrue(is_automation_paused(self.state))
        self.assertEqual(pending_links([self.links[0]], 5, self.state), [])
        self.assertEqual(pending_links([self.links[0]], 1, self.state), [self.links[0]])


if __name__ == "__main__":
    unittest.main()
