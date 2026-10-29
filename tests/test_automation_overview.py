import copy
import tkinter as tk
import unittest
from unittest.mock import Mock

from dashboard_model import automation_cards, snapshot
from operations_view import AutomationOverview
from agenda_view import AgendaView


class AutomationStateTests(unittest.TestCase):
    def setUp(self):
        self.now=100000
        self.config=dict(paused=False,enabled=True,away_mode=True,automatic_file_recovery=True,
                         shared_controls={'questions':True,'approvals':True},
                         allowed_project_roots=['C:/Example'],idle_seconds_before_actions=300,
                         apps={app:{'process_path':'app.exe','enabled':False} for app in ('codex','claude')})
        self.health={'reader':{'state':'active','updated_at':self.now,'last_successful_scan':self.now-10},
                     'supervisor':{'updated_at':self.now,'last_chat_checks':{'codex':self.now-30,'claude':self.now-40}},
                     'recovery':{'updated_at':self.now,'action':'idle'}}

    def cards(self):
        return {item['id']:item for item in automation_cards(self.config,self.health,self.now)}

    def test_fresh_functional_evidence_and_shared_flags_make_features_ready(self):
        before=copy.deepcopy((self.config,self.health))
        cards=self.cards()
        self.assertTrue(all(c['enabled'] and c['tone']=='good' for c in cards.values()))
        self.assertEqual(cards['notifications']['badge'],'In ascolto')
        self.assertEqual(cards['replies']['badge'],'Pronte')
        self.assertEqual((self.config,self.health),before)

    def test_pause_leaves_notification_reading_active_but_suspends_actions(self):
        self.config['paused']=True
        cards=self.cards()
        self.assertEqual(cards['notifications']['badge'],'In ascolto')
        for key in ('chats','replies','recovery'):
            self.assertTrue(cards[key]['enabled'])
            self.assertEqual(cards[key]['badge'],'In pausa')

    def test_disabled_options_remain_disabled_even_with_healthy_processes(self):
        self.config.update(away_mode=False,automatic_file_recovery=False)
        cards=self.cards()
        for key in ('replies','recovery'):
            self.assertFalse(cards[key]['enabled'])
            self.assertEqual(cards[key]['tone'],'off')
        self.assertEqual(cards['chats']['tone'],'good')

    def test_present_user_does_not_disable_reading_or_mislabel_enabled_actions(self):
        self.health['supervisor']['blocked_reason']='PC in uso: attendo 300 secondi senza mouse o tastiera'
        cards=self.cards()
        self.assertEqual(cards['chats']['tone'],'good')
        for key in ('replies','recovery'):
            self.assertTrue(cards[key]['enabled'])
            self.assertEqual(cards[key]['badge'],'In attesa')
            self.assertIn('Opzione attiva',cards[key]['detail'])
            self.assertIn('5 min',cards[key]['meta'])

    def test_living_supervisor_without_recent_scans_does_not_claim_chat_checks_work(self):
        self.health['supervisor']['last_chat_checks']={'codex':1,'claude':2}
        self.assertEqual(self.cards()['chats']['badge'],'Da verificare')
        self.health['supervisor']['updated_at']=1
        for key in ('chats','replies','recovery'):
            self.assertEqual(self.cards()[key]['tone'],'warning')

    def test_missing_reader_scan_and_partial_app_availability_are_visible(self):
        self.health['reader'].pop('last_successful_scan')
        self.assertNotEqual(self.cards()['notifications']['tone'],'good')
        self.health['supervisor']['waiting_for_apps']=['claude']
        self.assertEqual(self.cards()['chats']['badge'],'Parziale')
        self.assertIn('Claude',self.cards()['chats']['detail'])

    def test_unknown_recovery_and_missing_delegation_cannot_be_reported_ready(self):
        self.health['recovery']['action']='uncertain'
        self.health['supervisor']['blocked_reason']='PC in uso'
        self.assertEqual(self.cards()['recovery']['badge'],'Da verificare')
        self.config['allowed_project_roots']=[]
        self.assertEqual(self.cards()['recovery']['badge'],'Da configurare')
        self.assertTrue(self.cards()['recovery']['enabled'])

    def test_stopped_reader_and_stale_recovery_have_independent_states(self):
        self.health['reader']['requested']=False
        self.health['recovery']['updated_at']=1
        cards=self.cards()
        self.assertFalse(cards['notifications']['enabled'])
        self.assertEqual(cards['recovery']['badge'],'Da verificare')
        self.assertEqual(cards['replies']['badge'],'Pronte')


class AutomationLayoutTests(unittest.TestCase):
    def test_cards_and_ten_schedule_rows_keep_the_footer_visible_at_small_and_large_sizes(self):
        root=tk.Tk();root.withdraw();root.attributes('-alpha',0);root.attributes('-disabled',True)
        self.addCleanup(root.destroy)
        body=tk.Frame(root);body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1);body.rowconfigure(1,weight=1)
        cards=AutomationOverview(body,Mock());cards.grid(row=0,column=0,sticky='ew',pady=(0,20))
        agenda=AgendaView(body,Mock(),Mock(),Mock(),Mock());agenda.grid(row=1,column=0,sticky='nsew')
        config={'apps':{},'paused':False}
        jobs=[dict(id=str(i),version='v',title='Message '+str(i),enabled=True,activated_at=100000,
                   interval_seconds=1800+i*60,repeat=True) for i in range(10)]
        model=snapshot(config,jobs,[],[],{},100000)
        cards.render(model['automations']);agenda.render(model)
        root.deiconify()
        for width,height in [(800,650),(1040,710),(1300,760)]:
            with self.subTest(width=width):
                root.geometry(f'{width}x{height}');root.update();root.update_idletasks()
                self.assertGreater(agenda.canvas.winfo_height(),40)
                self.assertLessEqual(agenda.footer.winfo_y()+agenda.footer.winfo_height(),agenda.winfo_height()+1)
                self.assertTrue(agenda.scroll.winfo_ismapped())
                for row in agenda.rows.values():
                    self.assertEqual(row['badge'].cget('text'),'Programmato')
                    self.assertLessEqual(row['badge'].winfo_height(),34)
                for card in cards.cards.values():
                    self.assertLessEqual(card.winfo_x()+card.winfo_width(),cards.body.winfo_width()+1)
                    self.assertLessEqual(card.meta.winfo_y()+card.meta.winfo_height(),card.winfo_height())
                    self.assertEqual(card.title.cget('text'),card.model['title'])
        self.assertEqual(len(agenda.rows),10)

    def test_long_operational_labels_wrap_inside_rows_without_loss_at_supported_widths(self):
        root=tk.Tk();root.withdraw();root.attributes('-alpha',0);root.attributes('-disabled',True)
        self.addCleanup(root.destroy)
        agenda=AgendaView(root,Mock(),Mock(),Mock(),Mock());agenda.pack(fill='both',expand=True)
        jobs=[dict(id=str(i),version='v',title='Chat di progetto con un titolo lungo e completo '+str(i),
                   enabled=True,activated_at=100000,interval_seconds=1800,repeat=True) for i in range(3)]
        states={j['id']:dict(version='v',due=99900,phase='idle',detail='Chat non trovata o titolo ambiguo') for j in jobs}
        model=snapshot({'apps':{}},jobs,states,[],{},100000);agenda.render(model);root.deiconify()
        for width in (796,1040,1300):
            root.geometry(f'{width}x650');root.update();root.update_idletasks()
            for row in agenda.rows.values():
                for key,expected in [('badge','Destinatario non disponibile'),('countdown','Controlla il destinatario'),
                                     ('title',row['record']['title']),('receipt','Ricevuta non disponibile')]:
                    label=row[key]
                    self.assertEqual(label.cget('text'),expected)
                    self.assertLessEqual(label.winfo_reqheight(),label.winfo_height()+1)
                    self.assertLessEqual(label.winfo_y()+label.winfo_height(),row['inner'].winfo_height()+1)
                self.assertLessEqual(row['actions'].winfo_x()+row['actions'].winfo_width(),row['inner'].winfo_width()+1)
