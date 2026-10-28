import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from automation_policy import action_block_reason, app_enabled, title_allowed
from chat_desktop import step
from messages_panel import MessagesPanel


class PolicyTests(unittest.TestCase):
    def test_claude_unread_and_question_badges_keep_exact_title_and_group(self):
        from chat_desktop import entries
        for label in ('Regia Risposta non letta Bozza · 2 pull request', 'Bloccata Regia Ha fatto una domanda'):
            heading = Mock(Name='Project', AriaRole='heading', ControlTypeName='TextControl')
            group = Mock(Name='Project', AriaRole='', ControlTypeName='ButtonControl')
            row = Mock(Name=label, AriaRole='', ControlTypeName='ButtonControl')
            options = Mock(Name='Altre opzioni per Regia', AriaRole='', ControlTypeName='ButtonControl')
            unrelated = Mock(Name='Regia altro progetto', AriaRole='', ControlTypeName='ButtonControl')
            with patch('chat_desktop.walk', return_value=[]):
                result = entries([heading, group, row, options, unrelated], 'claude')
            self.assertEqual(len(result), 1)
            self.assertEqual((result[0][0]['title'], result[0][0]['group']), ('Regia', 'Project'))
            self.assertFalse(result[0][0]['ambiguous'])

    def test_row_toggle_targets_exact_id_and_does_not_reset_uncertain_timer(self):
        import tempfile
        from pathlib import Path
        from message_engine import TimerStore
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'logs').mkdir()
            one = dict(id='one', version='v1', title='Same title', app='claude', group='Test', message='Literal', enabled=True, activated_at=100, interval_seconds=900)
            other = {**one, 'id': 'other'}
            store = TimerStore(root / 'logs/messages.sqlite3')
            store.prepare(one, 100)
            store.update(one, 'uncertain', 'reserved', due=1000, sent=100)
            before = dict(store.rows()[0])
            store.db.close()
            view = SimpleNamespace(status=Mock(), render_jobs=Mock())
            with patch('messages_panel.ROOT', root), patch('messages_panel.load_jobs', return_value=[one, other]), patch('messages_panel.save_jobs') as save:
                MessagesPanel.set_enabled(view, False, job_ids=('one',))
            self.assertFalse(save.call_args.args[0][0]['enabled'])
            self.assertTrue(save.call_args.args[0][1]['enabled'])
            store = TimerStore(root / 'logs/messages.sqlite3')
            self.assertEqual(dict(store.rows()[0]), before)
            store.db.close()

    def test_double_click_pause_is_debounced_without_toggling_twice(self):
        view = SimpleNamespace(set_enabled=Mock())
        with patch('messages_panel.load_jobs', return_value=[{'id':'one','enabled':True}]), patch('messages_panel.time.monotonic', side_effect=[1, 1.1]):
            MessagesPanel.toggle_job_id(view, 'one')
            MessagesPanel.toggle_job_id(view, 'one')
        view.set_enabled.assert_called_once_with(False, job_ids=('one',))

    def test_changing_delivery_options_preserves_timer_version_and_activation(self):
        old={'id':'one','version':'v1','activated_at':100,'app':'claude','title':'Test','group':'Project',
             'enabled':True,'message':'hello','repeat':True,'interval_seconds':900,'send_when_active':False}
        view=SimpleNamespace(targets=[old],minutes=Mock(),message=Mock(),repeat=Mock(),send_when_active=Mock(),
                             send_when_present=Mock(),status=Mock(),render_jobs=Mock(),show_list=Mock(), selected_job=None, editor_signature=Mock(return_value=()), job_list=Mock())
        view.minutes.get.return_value='15';view.message.get.return_value='hello';view.repeat.get.return_value=True
        view.send_when_active.get.return_value=True;view.send_when_present.get.return_value=True
        with patch('messages_panel.load_jobs',return_value=[old]),patch('messages_panel.save_jobs') as save, \
             patch('messages_panel.load_config',return_value={}),patch('messages_panel.TimerStore'):
            MessagesPanel.activate(view)
        saved=save.call_args.args[0][0]
        self.assertEqual((saved['version'],saved['activated_at']),('v1',100))
        self.assertTrue(saved['send_when_present']);self.assertTrue(saved['send_when_active'])

    def test_claude_running_badge_does_not_hide_the_exact_scheduled_chat(self):
        from chat_desktop import entries
        heading = Mock(Name='project', AriaRole='heading', ControlTypeName='TextControl')
        group = Mock(Name='project', AriaRole='', ControlTypeName='ButtonControl')
        row = Mock(Name='Project regia 27-10 In esecuzione Bozza', AriaRole='', ControlTypeName='ButtonControl')
        options = Mock(Name='Altre opzioni per Project regia 27-10', AriaRole='', ControlTypeName='ButtonControl')
        with patch('chat_desktop.walk', return_value=[]):
            result = entries([heading, group, row, options], 'claude')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0]['title'], 'Project regia 27-10')
        self.assertEqual(result[0][0]['status'], 'In esecuzione')
        self.assertFalse(result[0][0]['ambiguous'])
    def test_resume_preserves_version_deadline_and_uncertain_reservation(self):
        existing = {'id': 'scheduled', 'version': 'same', 'activated_at': 123, 'enabled': False, 'title': 'Test'}
        view = SimpleNamespace(job_list=Mock(), status=Mock(), render_jobs=Mock())
        view.job_list.selection.return_value = ['scheduled']
        with patch('messages_panel.load_jobs', return_value=[existing]), patch('messages_panel.save_jobs') as save, patch('messages_panel.TimerStore'):
            MessagesPanel.set_enabled(view, True)
        self.assertEqual(save.call_args.args[0][0], {**existing, 'enabled': True})
        self.assertEqual(existing['version'], 'same')
        self.assertEqual(existing['activated_at'], 123)

    def test_send_while_active_is_saved_for_both_apps(self):
        targets = [{"app": app, "title": "Test " + app, "group": "Recenti", "thread_id": "pinned"} for app in ("codex", "claude")]
        view = SimpleNamespace(targets=targets, minutes=Mock(), message=Mock(), repeat=Mock(), send_when_active=Mock(), status=Mock(), render_jobs=Mock(), show_list=Mock(), selected_job=None, editor_signature=Mock(return_value=()), job_list=Mock())
        view.minutes.get.return_value = "30"
        view.message.get.return_value = "Saved literal"
        view.repeat.get.return_value = True
        for enabled in (True, False):
            view.send_when_active.get.return_value = enabled
            with patch("messages_panel.load_jobs", return_value=[]), patch("messages_panel.save_jobs") as save, patch("messages_panel.load_config", return_value={}), patch("messages_panel.TimerStore"):
                MessagesPanel.activate(view)
            self.assertEqual([j["send_when_active"] for j in save.call_args.args[0]], [enabled, enabled])

    def test_active_claude_uses_send_and_preserves_draft(self):
        job = {"app": "claude", "title": "Test", "group": "Sessioni", "ambiguous": False, "message": "Literal", "send_when_active": True}
        row = {**job, "status": "Working"}
        edit = Mock(ControlTypeName="EditControl", Name="Prompt", IsEnabled=True, AriaRole="")
        edit.GetValuePattern.return_value.Value = "Literal"
        stop = Mock(ControlTypeName="ButtonControl", Name="Interrompi", IsEnabled=True, AriaRole="")
        send = Mock(ControlTypeName="ButtonControl", Name="Invia", IsEnabled=True, AriaRole="")
        with patch("chat_desktop.snapshot", return_value=([edit, stop], "Test")), patch("chat_desktop.entries", return_value=[(row, Mock())]), patch("chat_desktop.question_card", return_value=None), patch("chat_desktop.walk", return_value=[stop, send]):
            self.assertIs(step({"idle_seconds_before_actions": 0}, job, {"drafted": True}, live_guard=lambda: None)["button"], send)
            job["send_when_active"] = False
            self.assertIn("elaborazione", step({"idle_seconds_before_actions": 0}, job, {"drafted": True})["reason"])
            stop.GetInvokePattern.assert_not_called()

    def test_active_codex_uses_only_its_steer_button_and_preserves_foreign_draft(self):
        job = {"app": "codex", "title": "Regia", "group": "Recenti", "ambiguous": False, "message": "Literal", "send_when_active": True}
        row = {**job, "status": "In elaborazione"}
        edit = Mock(ControlTypeName="EditControl", Name="Lavora con Codex", IsEnabled=True, AriaRole="")
        edit.GetValuePattern.return_value.Value = "Literal"
        stop = Mock(ControlTypeName="ButtonControl", Name="Interrompi", IsEnabled=True, AriaRole="")
        steer = Mock(ControlTypeName="ButtonControl", Name="Reindirizza", IsEnabled=True, AriaRole="")
        with patch("chat_desktop.snapshot", return_value=([edit, stop], "Regia")), patch("chat_desktop.entries", return_value=[(row, Mock())]), patch("chat_desktop.question_card", return_value=None), patch("chat_desktop.walk", return_value=[stop, steer]):
            result = step({"idle_seconds_before_actions": 0}, job, {"drafted": True}, live_guard=lambda: None)
            self.assertIs(result["button"], steer)
            stop.GetInvokePattern.assert_not_called()
            edit.GetValuePattern.return_value.Value = "User draft"
            result = step({"idle_seconds_before_actions": 0}, job, {"drafted": True}, live_guard=lambda: None)
            self.assertIn("Bozza presente", result["reason"])
            edit.GetValuePattern.return_value.SetValue.assert_not_called()

    def test_background_navigation_with_unknown_foreground_does_not_select(self):
        job = {"app": "codex", "title": "Regia", "group": "Recenti", "ambiguous": False, "message": "Literal", "send_when_active": True}
        button = Mock()
        with patch("chat_desktop.snapshot", return_value=([], "Other chat")), patch("chat_desktop.entries", return_value=[(job, button)]), \
             patch('automation_policy.idle_seconds', return_value=0), patch('automation_policy.foreground_app_state', return_value=None):
            result = step({"idle_seconds_before_actions": 0, "native_background_delivery": True}, job, {}, live_guard=lambda: None)
        self.assertIn("Impossibile verificare", result["reason"])
        button.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_native_delivery_explicit_present_is_independent_of_active_chat(self):
        from automation_policy import native_delivery_config
        for app in ('codex', 'claude'):
            config = {'idle_seconds_before_actions': 900, 'apps': {app: {'process_path': 'C:/Test/app.exe'}}}
            with patch('automation_policy.idle_seconds', return_value=0):
                allowed = native_delivery_config(lambda: config, app=app, allow_present=True)
                self.assertTrue(allowed['native_background_delivery'])
                self.assertEqual(allowed['idle_seconds_before_actions'], 0)
                self.assertEqual(allowed['native_navigation_idle_seconds'], 900)
                self.assertEqual(native_delivery_config(lambda: config, app=app, allow_present=False), config)
                paused = {**config, 'paused': True}
                self.assertEqual(native_delivery_config(lambda: paused, app=app, allow_present=True), paused)

    def test_both_apps_select_exact_running_chat_in_background_without_focus(self):
        for app in ('codex', 'claude'):
            job = {'app': app, 'title': 'Exact target', 'group': 'Project', 'ambiguous': False,
                   'message': 'Literal', 'send_when_active': True, 'send_when_present': True}
            control = Mock()
            with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
                 patch('chat_desktop.entries', return_value=[({**job, 'status': 'Working'}, control)]), \
                 patch('automation_policy.idle_seconds', return_value=0), \
                 patch('automation_policy.foreground_app_state', return_value=False):
                result = step({'idle_seconds_before_actions': 0, 'native_background_delivery': True}, job, {}, live_guard=lambda: None)
            self.assertEqual(result['action'], 'selected_chat')
            control.GetInvokePattern.return_value.Invoke.assert_called_once_with()
            control.SetFocus.assert_not_called()
            control.Click.assert_not_called()

    def test_foreground_other_chat_returns_specific_reason_without_navigation(self):
        for app in ('codex', 'claude'):
            job = {'app': app, 'title': 'Exact target', 'group': 'Project', 'ambiguous': False,
                   'message': 'Literal', 'send_when_active': True, 'send_when_present': True}
            control = Mock()
            with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
                 patch('chat_desktop.entries', return_value=[(job, control)]), \
                 patch('automation_policy.idle_seconds', return_value=0), \
                 patch('automation_policy.foreground_app_state', return_value=True):
                result = step({'idle_seconds_before_actions': 0, 'native_background_delivery': True}, job, {}, live_guard=lambda: None)
            self.assertIn('Applicazione in uso su un’altra chat', result['reason'])
            control.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_present_and_active_flags_allow_ready_on_same_foreground_chat(self):
        for app, label in (('codex', 'Lavora con Codex'), ('claude', 'Prompt')):
            job = {'app': app, 'title': 'Exact target', 'group': 'Project', 'ambiguous': False,
                   'message': 'Literal', 'send_when_active': True, 'send_when_present': True}
            edit = Mock(ControlTypeName='EditControl', Name=label, IsEnabled=True, AriaRole='', HasKeyboardFocus=False)
            edit.GetValuePattern.return_value.Value = 'Literal'
            stop = Mock(ControlTypeName='ButtonControl', Name='Interrompi', IsEnabled=True, AriaRole='')
            send = Mock(ControlTypeName='ButtonControl', Name='Invia', IsEnabled=True, AriaRole='')
            with patch('chat_desktop.snapshot', return_value=([edit, stop], 'Exact target')), \
                 patch('chat_desktop.entries', return_value=[({**job, 'status': 'Working'}, Mock())]), \
                 patch('chat_desktop.question_card', return_value=None), patch('chat_desktop.walk', return_value=[stop, send]), \
                 patch('automation_policy.foreground_app_state', return_value=True) as foreground:
                result = step({'idle_seconds_before_actions': 0, 'native_background_delivery': True},
                              job, {'drafted': True}, live_guard=lambda: None)
                self.assertEqual(result['action'], 'ready')
                self.assertTrue(result['validate_draft']())
                foreground.assert_not_called()  # Same chat does not navigate.
            stop.GetInvokePattern.assert_not_called()
            send.GetInvokePattern.assert_not_called()

    def test_foreground_race_while_acquiring_invoke_pattern_is_rechecked(self):
        foreground = {'value': False}
        control = Mock()
        invoke = Mock()
        def acquire():
            foreground['value'] = True
            return invoke
        control.GetInvokePattern.side_effect = acquire
        job = {'app': 'claude', 'title': 'Exact target', 'group': 'Project', 'ambiguous': False,
               'send_when_active': True, 'send_when_present': True}
        with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
             patch('chat_desktop.entries', return_value=[(job, control)]), \
             patch('automation_policy.idle_seconds', return_value=0), \
             patch('automation_policy.foreground_app_state', side_effect=lambda *_: foreground['value']):
            result = step({'idle_seconds_before_actions': 0, 'native_background_delivery': True}, job, {}, live_guard=lambda: None)
        self.assertIn('Applicazione in uso', result['reason'])
        invoke.Invoke.assert_not_called()

    def test_pause_race_immediately_before_native_selection_is_honored(self):
        control = Mock()
        job = {'app': 'codex', 'title': 'Exact target', 'group': 'Project', 'ambiguous': False}
        guard = Mock(side_effect=[None, None, 'Sentinella in pausa globale'])
        with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
             patch('chat_desktop.entries', return_value=[(job, control)]):
            result = step({'idle_seconds_before_actions': 0}, job, {}, live_guard=guard)
        self.assertIn('pausa', result['reason'])
        control.GetInvokePattern.return_value.Invoke.assert_not_called()

    def test_navigation_after_real_inactivity_still_works_if_app_is_foreground(self):
        control = Mock()
        job = {'app': 'claude', 'title': 'Exact target', 'group': 'Project', 'ambiguous': False}
        with patch('chat_desktop.snapshot', return_value=([], 'Other chat')), \
             patch('chat_desktop.entries', return_value=[(job, control)]), \
             patch('automation_policy.idle_seconds', return_value=31), \
             patch('automation_policy.foreground_app_state', return_value=True):
            result = step({'idle_seconds_before_actions': 0, 'native_background_delivery': True,
                           'native_navigation_idle_seconds': 30}, job, {}, live_guard=lambda: None)
        self.assertEqual(result['action'], 'selected_chat')

    def test_send_validation_rechecks_the_chat_instead_of_a_stale_composer(self):
        job = {'app': 'claude', 'title': 'Exact target', 'group': 'Project', 'ambiguous': False,
               'message': 'Literal', 'send_when_active': True}
        edit = Mock(ControlTypeName='EditControl', Name='Prompt', IsEnabled=True, AriaRole='', HasKeyboardFocus=False)
        edit.GetValuePattern.return_value.Value = 'Literal'
        send = Mock(ControlTypeName='ButtonControl', Name='Invia', IsEnabled=True)
        with patch('chat_desktop.snapshot', side_effect=[([edit], 'Exact target'), ([edit], 'Other chat')]), \
             patch('chat_desktop.entries', return_value=[(job, Mock())]), \
             patch('chat_desktop.question_card', return_value=None), patch('chat_desktop.walk', return_value=[send]):
            result = step({'idle_seconds_before_actions': 0}, job, {'drafted': True}, live_guard=lambda: None)
            self.assertEqual(result['action'], 'ready')
            self.assertFalse(result['validate_draft']())
        send.GetInvokePattern.assert_not_called()

    def test_reselecting_sidebar_row_preserves_existing_delivery_identity_and_policy(self):
        existing = {"id": "regia", "version": "old", "app": "codex", "title": "Regia", "group": "Recenti", "thread_id": "pinned-thread", "transport": "codex_app", "send_when_active": True}
        target = {"app": "codex", "title": "Regia", "group": "Recenti"}
        view = SimpleNamespace(targets=[target], minutes=Mock(), message=Mock(), repeat=Mock(), status=Mock(), render_jobs=Mock(), show_list=Mock(), selected_job=None, editor_signature=Mock(return_value=()), job_list=Mock())
        view.minutes.get.return_value = "30"
        view.message.get.return_value = "Literal user message"
        view.repeat.get.return_value = True
        with patch("messages_panel.load_jobs", return_value=[existing]), patch("messages_panel.save_jobs") as save, patch("messages_panel.load_config", return_value={"paused": True}), patch("messages_panel.TimerStore"):
            MessagesPanel.activate(view)
        saved = save.call_args.args[0][0]
        for key in ["id", "thread_id", "transport", "send_when_active"]:
            self.assertEqual(saved[key], existing[key])
        self.assertNotEqual(saved["version"], existing["version"])

    def test_question_scope_is_independent_of_approval_scope(self):
        config = {"apps": {"codex": {"enabled": True, "target_titles": ["Project Regia"], "question_target_titles": []}}}
        self.assertTrue(title_allowed(config, "codex", "Scegliere il CMS per il sito", "questions"))
        self.assertFalse(title_allowed(config, "codex", "Scegliere il CMS per il sito", "approvals"))

    def test_both_apps_can_monitor_questions_independently(self):
        config = {"apps": {"codex": {"questions_enabled": True}, "claude": {"enabled": False, "questions_enabled": True}}}
        for app in config["apps"]:
            self.assertTrue(app_enabled(config, app, "questions"))
            self.assertTrue(title_allowed(config, app, "Qualsiasi chat", "questions"))

    def test_global_pause_and_recent_human_input_block_actions(self):
        self.assertIsNotNone(action_block_reason({"paused": True}, observed_idle=9999))
        for seconds in (0, 1, 10, 29.9):
            self.assertIsNotNone(action_block_reason({}, observed_idle=seconds))
        self.assertIsNone(action_block_reason({}, observed_idle=30))

    def test_busy_background_chat_is_not_selected(self):
        target = {"app": "codex", "title": "Project Regia", "group": "Recenti", "ambiguous": False, "status": "In elaborazione"}
        button = Mock()
        with patch("chat_desktop.snapshot", return_value=([], "Scegliere il CMS per il sito")), patch("chat_desktop.entries", return_value=[(target, button)]):
            result = step({"idle_seconds_before_actions": 0}, target, {})
        button.GetInvokePattern.assert_not_called()
        self.assertEqual(result["action"], "none")
        self.assertIn("senza cambiare chat", result["reason"])

    def test_paused_timer_does_not_even_query_other_windows(self):
        with patch("chat_desktop.snapshot") as snapshot:
            result = step({"paused": True}, {"app": "codex"}, {})
        snapshot.assert_not_called()
        self.assertIn("pausa", result["reason"])

    def test_multiple_selected_chats_create_independent_timers(self):
        targets = [{"app": app, "title": title, "group": "Recenti", "thread_id": "test-thread"} for app, title in (("codex", "Regia"), ("claude", "Lavoro"))]
        view = SimpleNamespace(targets=targets, minutes=Mock(), message=Mock(), repeat=Mock(), status=Mock(), render_jobs=Mock(), show_list=Mock(), selected_job=None, editor_signature=Mock(return_value=()), job_list=Mock())
        view.minutes.get.return_value = "15"
        view.message.get.return_value = "Continua con il lavoro autorizzato."
        view.repeat.get.return_value = True
        with patch("messages_panel.load_jobs", return_value=[]), patch("messages_panel.save_jobs") as save, patch("messages_panel.load_config", return_value={"paused": True}), patch("messages_panel.TimerStore"):
            MessagesPanel.activate(view)
        jobs = save.call_args.args[0]
        self.assertEqual({j["app"] for j in jobs}, {"codex", "claude"})
        self.assertEqual(len({j["id"] for j in jobs}), 2)
        self.assertTrue(all(j["interval_seconds"] == 900 for j in jobs))

    def test_chat_selections_survive_switching_app_filter(self):
        codex = {"app": "codex", "title": "Regia", "group": "Recenti"}
        claude = {"app": "claude", "title": "Lavoro", "group": "Sessioni"}
        view = SimpleNamespace(rendering_chats=False, editor_visible=True, editor_step=1, targets=[codex], chats={"0": claude}, chat_list=Mock(), identity=MessagesPanel.identity, update_target_summary=Mock())
        view.chat_list.selection.return_value = ("0",)
        MessagesPanel.choose_chat(view)
        self.assertEqual(view.targets, [codex, claude])
        view.chats = {"0": codex}
        view.chat_list.selection.return_value = ()
        MessagesPanel.choose_chat(view)
        self.assertEqual(view.targets, [claude])
