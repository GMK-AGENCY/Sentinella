"""Native window selection: auxiliary panes must not disable real chat windows."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chat_desktop import snapshot


class Node:
    IsEnabled = True
    IsOffscreen = False
    AriaRole = ''
    def __init__(self, kind, name='', value=''):
        self.ControlTypeName, self.Name, self.value = kind, name, value
    def GetValuePattern(self):return SimpleNamespace(Value=self.value)
    def GetRuntimeId(self):return [id(self)]
    def GetParentControl(self):return self.parent


TARGET = '11111111-2222-4333-8444-555555555555'
OTHER = '66666666-7777-4888-8999-aaaaaaaaaaaa'
def claude(identifier=TARGET, title='Chat'):
    doc=Node('DocumentControl',title+' - Claude Code','https://claude.ai/epitaxy/local_'+identifier)
    prompt=Node('EditControl','Prompt');prompt.parent=doc
    return [doc,prompt]


class WindowRoutingTests(unittest.TestCase):
    def read(self, windows, job=None):
        config={'apps':{'claude':{'process_path':'Claude.exe'}}}
        auto=SimpleNamespace(SetGlobalSearchTimeout=lambda _:None,ControlFromHandle=lambda handle:handle)
        with patch.dict(sys.modules, {'uiautomation':auto}), patch('chat_desktop.desktop_is_unlocked',return_value=True), \
             patch('chat_desktop.app_window_handles',return_value=list(range(len(windows)))), \
             patch('chat_desktop.native_controls',side_effect=lambda handle:windows[handle]):
            return snapshot(config,'claude',job) if job is not None else snapshot(config,'claude')

    def test_one_chat_and_two_auxiliary_windows_is_a_valid_chat(self):
        expected=claude()
        controls,title=self.read([[Node('WindowControl','Menu')],expected,[Node('WindowControl','Tooltip')]])
        self.assertIs(controls,expected)
        self.assertEqual(title,'Chat')

    def test_target_id_selects_the_correct_window_without_using_its_name(self):
        target=claude(TARGET,'Renamed')
        controls,title=self.read([claude(OTHER,'Chat'),target],{'app':'claude','session_id':TARGET,'title':'Old title'})
        self.assertEqual(controls,target)
        self.assertEqual(title,'Renamed')

    def test_duplicate_open_target_is_an_error_not_an_arbitrary_choice(self):
        with self.assertRaisesRegex(RuntimeError,'univoc'):
            self.read([claude(),claude()],{'app':'claude','session_id':TARGET,'title':'Chat'})

    def test_browser_preview_is_not_a_second_chat(self):
        browser=[Node('DocumentControl','Website','https://example.com'),Node('EditControl','Prompt')]
        expected=claude()
        self.assertIs(self.read([browser,expected])[0],expected)

    def test_no_chat_is_reported_as_unavailable(self):
        with self.assertRaises(RuntimeError):self.read([[Node('WindowControl','Tooltip')]])

    def test_target_document_excludes_another_pane_and_its_composer(self):
        first=claude(TARGET,'Target');other=claude(OTHER,'Other')
        controls,title=self.read([first+other],{'app':'claude','session_id':TARGET,'title':'Target'})
        self.assertEqual(controls,first)
        self.assertEqual(title,'Target')

    def test_guest_browser_inside_a_chat_does_not_supply_controls(self):
        first=claude();guest=Node('DocumentControl','Website','https://example.com');guest.parent=first[0]
        fake=Node('EditControl','Prompt');fake.parent=guest
        controls,_=self.read([first+[guest,fake]],{'app':'claude','session_id':TARGET,'title':'Chat'})
        self.assertEqual(controls,first)

    def test_seven_windows_with_same_name_use_only_the_exact_session(self):
        windows=[claude(OTHER,'Same name') for _ in range(6)]+[claude(TARGET,'Same name')]
        controls,_=self.read(windows,{'app':'claude','session_id':TARGET,'title':'Same name'})
        self.assertEqual(controls,windows[-1])

    def test_popout_panes_of_same_session_do_not_count_as_second_composers(self):
        chat=claude()
        files=[claude()[0],Node('ListControl','Files')]
        diff=[claude()[0],Node('DocumentControl','Diff','https://example.com')]
        controls,_=self.read([files,chat,diff],{'app':'claude','session_id':TARGET,'title':'Chat'})
        self.assertEqual(controls,chat)


if __name__=='__main__':unittest.main()
