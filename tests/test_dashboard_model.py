import copy
import unittest

from dashboard_model import snapshot


class DashboardModelTests(unittest.TestCase):
    def test_dashboard_uses_the_same_operational_state_as_schedule_list(self):
        from schedule_display import schedule_display
        for phase, detail in [('idle',''),('drafted',''),('uncertain','Bozza scomparsa'),
                              ('uncertain','Messaggio accodato alla chat: attendo'),('idle','Bozza presente')]:
            state={'version':self.job['version'],'phase':phase,'detail':detail,'due':self.now+10}
            direct=schedule_display(self.job,state,{},self.now)
            row=snapshot({},[self.job],{self.job['id']:state},[],{},self.now)['schedule'][0]
            self.assertEqual((row['status'],row['status_key'],row['countdown']),
                             (direct['status'],direct['status_key'],direct['next_precise']))
    def setUp(self):
        self.now = 100000
        self.job = {"id": "active", "version": "v2", "title": "Current chat", "enabled": True, "interval_seconds": 1800, "activated_at": 99000}

    def test_next_is_current_version_and_finished_disabled_are_not_scheduled(self):
        disabled = {**self.job, "id": "old", "enabled": False}
        finished = {**self.job, "id": "done"}
        state = {"active": {"version": "v1", "due": 1}, "old": {"version": "v2", "phase": "uncertain", "due": 2}, "done": {"version": "v2", "phase": "finished", "due": 3}}
        result = snapshot({}, [disabled, finished, self.job], state, [], {}, self.now)
        self.assertEqual(result["next"]["when"], 100800)
        self.assertEqual(result["next"]["frequency"], "Ogni 30 min")
        self.assertEqual(result["next"]["countdown"], "Tra 13 min 20 s")
        self.assertFalse(result["attention"])
        self.assertEqual(result["inactive_attention"][0]["job_id"], "old")

    def test_global_pause_preserves_due_and_never_claims_send_will_happen(self):
        result = snapshot({"paused": True}, [self.job], [], [], {}, self.now)
        self.assertEqual(result["next"]["when"], 100800)
        self.assertEqual(result["next"]["countdown"], "Invii in pausa")
        self.assertTrue(result["paused"])
        result = snapshot({"paused": True}, [self.job], {"active": {"version": "v2", "due": 1}}, [], {}, self.now)
        self.assertEqual(result["attention"], [])

    def test_receipt_needed_not_last_sent_or_native_click(self):
        events = [{"job_id": "active", "title": "Current chat", "at": 99999, "event": "Invio effettuato"},
                  {"job_id": "active", "title": "Current chat", "at": 99000, "event": "Consegnato con ricevuta"}]
        result = snapshot({}, [self.job], [{"id": "active", "version": "v2", "last_sent": 99999}], events, {}, self.now)
        self.assertEqual(result["last_confirmed"]["at"], 99000)
        self.assertIsNone(snapshot({}, [], [], events[:1], {}, self.now)["last_confirmed"])

    def test_pending_receipt_is_distinct_from_old_uncertainty_and_next_job(self):
        pending = {**self.job, "id": "pending"}
        states = {"pending": {"version": "v2", "phase": "uncertain", "local_stage": "awaiting_receipt", "reserved_at": self.now - 30, "due": 1}}
        result = snapshot({}, [pending, self.job], states, [], {}, self.now)
        self.assertEqual(result["next"]["job_id"], "active")
        self.assertEqual(result["attention"][0]["severity"], "info")
        states["pending"]["reserved_at"] = self.now - 121
        self.assertEqual(snapshot({}, [pending], states, [], {}, self.now)["attention"][0]["severity"], "warning")

    def test_disabled_old_uncertainty_does_not_block_active_schedule(self):
        old = {**self.job, "id": "old", "enabled": False}
        states = {"old": {"version": "v2", "phase": "uncertain"}}
        result = snapshot({}, [old, self.job], states, [], {}, self.now)
        self.assertEqual(result["attention"], [])
        self.assertEqual(len(result["inactive_attention"]), 1)
        self.assertEqual(result["next"]["job_id"], "active")

    def test_user_draft_is_actionable_without_fake_delivery(self):
        states = {"active": {"version": "v2", "phase": "uncertain", "local_stage": "drafted", "due": 99900, "detail": "Bozza presente: non sovrascrivo"}}
        result = snapshot({}, [self.job], states, [], {}, self.now)
        self.assertEqual(result["attention"][0]["severity"], "warning")
        self.assertIn("bozza", result["attention"][0]["reason"])
        self.assertIsNone(result["last_confirmed"])

    def test_limited_history_never_becomes_exact_daily_count(self):
        events = [{"at": 99990, "event": "Consegnato con ricevuta"}] * 200
        result = snapshot({}, [], [], events, {}, self.now)
        self.assertIsNone(result["metrics"]["confirmed_24h"])
        envelope = {"events": events, "confirmed_24h": {"count": 321, "since": self.now - 86400, "until": self.now, "complete": True, "source": "SQL COUNT messages.sqlite3"}}
        self.assertEqual(snapshot({}, [], [], envelope, {}, self.now)["metrics"]["confirmed_24h"], 321)
        envelope["confirmed_24h"]["since"] = 0
        self.assertIsNone(snapshot({}, [], [], envelope, {}, self.now)["metrics"]["confirmed_24h"])

    def test_health_requires_function_activity_and_exposes_staleness_source(self):
        health = {"gui": {"updated_at": self.now, "poll_updated_at": self.now - 91},
                  "reader": {"updated_at": self.now},
                  "supervisor": {"updated_at": self.now - 2, "waiting_for_apps": ["one"]}}
        result = snapshot({}, [], [], [], health, self.now)
        self.assertEqual([row["status"] for row in result["components"]], ["stale", "unknown", "fresh"])
        self.assertEqual(result["components"][0]["limit_seconds"], 90)
        self.assertIn("gui-heartbeat", result["components"][0]["source"])
        self.assertIn("Attende", result["components"][2]["label"])

    def test_deterministic_and_does_not_mutate_input(self):
        arguments = ({}, [self.job], {}, [], {}, self.now)
        before = copy.deepcopy(arguments)
        self.assertEqual(snapshot(*arguments), snapshot(*arguments))
        self.assertEqual(arguments, before)

    def test_agenda_config_counts_stay_explicit_when_globally_paused(self):
        jobs = [self.job, {**self.job, "id": "second"}, {**self.job, "id": "off", "enabled": False}]
        result = snapshot({"paused": True}, jobs, {}, [], {}, self.now)
        self.assertEqual(result["counts"], {"total": 3, "enabled": 2, "paused": 1})
        self.assertTrue(all(row["status"] == "In pausa" for row in result["schedule"]))
        self.assertEqual(sum(row["enabled"] for row in result["schedule"]), 2)
        self.assertEqual(result["schedule"][0]["countdown"], "Nessun nuovo invio")

    def test_agenda_enabled_first_then_due_disabled_last_and_receipts_only(self):
        jobs = [{**self.job, "id": "off", "enabled": False}, self.job, {**self.job, "id": "first"}]
        states = {"first": {"version": "v2", "due": self.now + 10}, "off": {"version": "v2", "due": 1}, "active": {"version": "v2", "due": self.now + 20, "last_sent": self.now}}
        history = [{"job_id": "first", "title": "Old title", "event": "Consegnato con ricevuta", "at": self.now - 50},
                   {"job_id": "active", "title": "Current chat", "event": "Invio effettuato", "at": self.now}]
        rows = snapshot({}, jobs, states, history, {}, self.now)["schedule"]
        self.assertEqual([row["job_id"] for row in rows], ["first", "active", "off"])
        self.assertEqual(rows[0]["last_confirmed_at"], self.now - 50)
        self.assertIsNone(rows[1]["last_confirmed_at"])
        self.assertEqual(rows[2]["countdown"], "Nessun nuovo invio")

    def test_agenda_pending_and_draft_are_not_new_countdowns(self):
        jobs = [self.job, {**self.job, "id": "draft"}]
        states = {"active": {"version": "v2", "phase": "uncertain", "local_stage": "awaiting_receipt", "reserved_at": self.now - 10, "due": self.now - 10},
                  "draft": {"version": "v2", "phase": "uncertain", "local_stage": "drafted", "detail": "Bozza presente", "due": self.now - 20}}
        rows = {row["job_id"]: row for row in snapshot({}, jobs, states, [], {}, self.now)["schedule"]}
        self.assertEqual(rows["active"]["countdown"], "Attende la ricevuta")
        self.assertEqual(rows["draft"]["countdown"], "Campo messaggio già compilato")
        self.assertTrue(all(row["status"] in {"Conferma in corso", "Bozza nella chat"} for row in rows.values()))

    def test_ten_agenda_rows_do_not_collapse_same_titles_or_mutate_states(self):
        jobs = [{**self.job, "id": str(index), "enabled": index < 8} for index in range(10)]
        result = snapshot({}, jobs, {}, [], {}, self.now)
        self.assertEqual(len(result["schedule"]), 10)
        self.assertEqual(len({row["job_id"] for row in result["schedule"]}), 10)
        self.assertEqual(result["counts"], {"total": 10, "enabled": 8, "paused": 2})

    def test_owned_preparation_has_no_countdown_and_is_informational(self):
        state = {self.job['id']: {'version': self.job['version'], 'phase':'drafted', 'due':self.now+10}}
        result = snapshot({}, [self.job], state, [], {}, self.now)
        self.assertEqual(result['schedule'][0]['countdown'], 'Messaggio pronto da inviare')
        self.assertEqual(result['attention'][0]['severity'], 'info')


if __name__ == "__main__":
    unittest.main()
