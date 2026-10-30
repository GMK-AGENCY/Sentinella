"""Opaque about:blank surfaces need an app-owned ID binding, never a name guess."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from claude_popouts import match_session,root_window,session_for_document,visible_frame

SID='11111111-2222-4333-8444-555555555555'


class PopoutTests(unittest.TestCase):
    def setUp(self):
        self.frame=(-101,200,902,701)
        self.row=dict(sessionId='local_'+SID,sessionType='local',isSsh=False,title='Test',bounds=dict(x=-100,y=200,width=900,height=700))

    def test_app_record_and_visible_frame_identify_id_after_a_rename(self):
        self.assertEqual(match_session([self.row],'Test',self.frame),SID)
        self.assertIsNone(match_session([self.row],'Renamed',self.frame))
        renamed={**self.row,'title':'Renamed'}
        self.assertEqual(match_session([renamed],'Renamed',self.frame),SID)

    def test_duplicate_records_and_mismatched_geometry_do_not_guess(self):
        self.assertIsNone(match_session([self.row,self.row],'Test',self.frame))
        self.assertIsNone(match_session([self.row],'Test',(-90,200,900,700)))
        self.assertIsNone(match_session([self.row],'Another',self.frame))

    def test_invalid_remote_and_malformed_state_cannot_supply_an_id(self):
        for change in ({'sessionType':'remote'},{'isSsh':True},{'sessionId':'other'},
                       {'bounds':None},{'bounds':{'x':float('nan'),'y':0,'width':900,'height':700}}):
            with self.subTest(change=change):self.assertIsNone(match_session([{**self.row,**change}],'Test',self.frame))
        for frame in (None,[],[1,2,3],(0,0,True,700)):
            self.assertIsNone(match_session([self.row],'Test',frame))

    def test_embedded_document_cannot_inherit_popout_identity(self):
        document=Mock();document.GetParentControl.return_value=SimpleNamespace(ControlTypeName='DocumentControl')
        self.assertIsNone(root_window(document))

    def fixture(self):
        window=Mock(ControlTypeName='WindowControl',ClassName='Chrome_WidgetWin_1',Name='Test',NativeWindowHandle=10,ProcessId=123)
        document=Mock(Name='Test');document.GetParentControl.return_value=window
        return document

    def test_verified_window_binds_once_and_duplicate_window_is_rejected(self):
        document=self.fixture()
        with patch('psutil.Process') as process,patch('automation_policy.load_config',return_value={'apps':{'claude':{'process_path':'C:/App/claude.exe'}}}), \
             patch('claude_popouts.visible_frame',return_value=self.frame),patch('claude_popouts.records_for_executable',return_value=[self.row]), \
             patch('desktop_identity.app_window_handles',return_value=[10]) as handles,patch('uiautomation.ControlFromHandle',return_value=SimpleNamespace(Name='Test')):
            process.return_value.exe.return_value='C:/App/claude.exe'
            self.assertEqual(session_for_document(document),SID)
            handles.return_value=[10,11]
            self.assertIsNone(session_for_document(document))

    def test_wrong_process_and_unqualified_frame_are_rejected(self):
        document=self.fixture()
        with patch('psutil.Process') as process,patch('automation_policy.load_config',return_value={'apps':{'claude':{'process_path':'C:/App/claude.exe'}}}), \
             patch('claude_popouts.visible_frame',return_value=None) as frame,patch('claude_popouts.records_for_executable') as records:
            process.return_value.exe.return_value='C:/Other/claude.exe'
            self.assertIsNone(session_for_document(document));frame.assert_not_called()
            process.return_value.exe.return_value='C:/App/claude.exe'
            self.assertIsNone(session_for_document(document));records.assert_not_called()

    def test_unqualified_scale_and_minimized_windows_never_supply_geometry(self):
        for dpi,minimized in ((144,0),(96,1)):
            with self.subTest(dpi=dpi,minimized=minimized):
                user=SimpleNamespace(GetDpiForWindow=Mock(return_value=dpi),IsIconic=Mock(return_value=minimized))
                dwm=Mock()
                with patch('ctypes.WinDLL',side_effect=[user,dwm]):
                    self.assertIsNone(visible_frame(10))
                dwm.DwmGetWindowAttribute.assert_not_called()
