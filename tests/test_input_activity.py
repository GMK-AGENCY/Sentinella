"""Inactivity source qualification rejects stale, mismatched and unknown input."""
import unittest
from input_activity import trusted_idle,DeviceActivity


class InputActivityTests(unittest.TestCase):
    def setUp(self):
        self.state=dict(run_id='owner',source='input-hooks',tick=1000000,
                        last_device_tick=600000,all_input_tick=999999,last_hook_tick=999999)

    def test_software_input_does_not_reset_device_inactivity(self):
        self.assertEqual(trusted_idle(self.state,'owner',1000500,999999),400.5)

    def test_new_input_not_yet_observed_requires_conservative_fallback(self):
        self.assertIsNone(trusted_idle(self.state,'owner',1000500,1000001))

    def test_real_device_input_resets_inactivity(self):
        self.state.update(last_device_tick=1000000)
        self.assertEqual(trusted_idle(self.state,'owner',1000500,999999),.5)

    def test_stale_wrong_owner_and_failed_provider_are_not_authoritative(self):
        for change in ({'tick':990000},{'run_id':'previous instance'},{'source':'fallback'},
                       {'source':'stopped'},{'source':'starting'}):
            with self.subTest(change=change):
                self.assertIsNone(trusted_idle({**self.state,**change},'owner',1000500,999999))
        self.assertIsNone(trusted_idle(self.state,None,1000500,999999))

    def test_malformed_or_future_ticks_never_infer_absence(self):
        for change in ({'tick':float('nan')},{'tick':False},{'last_device_tick':float('inf')},
                       {'last_device_tick':-1},{'last_device_tick':1000001},{'tick':1001000}):
            with self.subTest(change=change):
                self.assertIsNone(trusted_idle({**self.state,**change},'owner',1000500,999999))

    def test_large_uptime_is_not_truncated_to_32_bits(self):
        now=2**32+1000000
        state={**self.state,'tick':now,'last_device_tick':now-400000}
        self.assertEqual(trusted_idle(state,'owner',now+500,999999),400.5)

    def test_missing_hook_event_cannot_be_hidden_by_a_fresh_publisher(self):
        self.state['last_hook_tick']=999998
        self.assertIsNone(trusted_idle(self.state,'owner',1000500,999999))

    def test_invalid_json_shape_uses_conservative_api(self):
        for record in ([],None,'text',1):
            self.assertIsNone(trusted_idle(record,'owner',1000500,999999))

    def test_native_origin_flags_distinguish_injection_without_reading_keys(self):
        observer=DeviceActivity('.','test')
        observer.last_device_tick=10
        observer.record_origin(0x10,100,0x10,100)
        observer.record_origin(0x01,101,0x01,101)
        self.assertEqual((observer.events,observer.injected,observer.last_device_tick),(0,2,10))
        observer.record_origin(0,102,0x10,102)
        self.assertEqual((observer.events,observer.injected,observer.last_device_tick),(1,2,102))


if __name__=='__main__':unittest.main()
