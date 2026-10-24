"""Managed-browser recovery must not close another job's tabs or lose its exclusive lease on failure."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('recover_assistant',Path(__file__).with_name('assistant.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class RecoveryLease(unittest.TestCase):
    def setUp(self):
        a.BROWSER_RECOVERED.clear()
        self.addCleanup(a.BROWSER_RECOVERED.clear)
    def run_with(self,busy=False,status=None,fail=False,config=None):
        calls=[]
        def fake(cmd,**kw):
            calls.append(cmd)
            if cmd[0]!='openclaw':
                return subprocess.CompletedProcess(cmd,3 if busy and 'claim' in cmd else 0,'','')
            if fail:raise OSError('temporary browser transport outage')
            return subprocess.CompletedProcess(cmd,0,json.dumps(status or {'running':False}),'')
        with patch.object(a.subprocess,'run',side_effect=fake):
            result=a.recover_browser('openclaw') if config is None else a.recover_browser('openclaw',config=config)
        return result,calls
    def test_busy_holder_is_not_interrupted_and_does_not_consume_cooldown(self):
        result,calls=self.run_with(busy=True)
        self.assertFalse(result)
        self.assertEqual(len(calls),1)
        self.assertIn('--exclusive',calls[0])
        self.assertNotIn('openclaw',a.BROWSER_RECOVERED)
    def test_restart_is_bracketed_by_the_same_exclusive_owner(self):
        result,calls=self.run_with(status={'running':True,'cdpReady':False,'cdpHttp':False})
        self.assertTrue(result)
        self.assertEqual([c[4:] for c in calls if c[0]=='openclaw'],[['--json','status'],['stop'],['start']])
        self.assertIn('claim',calls[0]);self.assertIn('release',calls[-1])
        self.assertEqual(calls[0][calls[0].index('--owner')+1],calls[-1][-1])
    def test_failure_and_healthy_browser_both_release_the_lease(self):
        for fail,status in ((True,None),(False,{'running':True,'cdpReady':True,'cdpHttp':True})):
            a.BROWSER_RECOVERED.clear()
            result,calls=self.run_with(fail=fail,status=status)
            self.assertFalse(result);self.assertIn('release',calls[-1])
    def test_configured_helper_is_used(self):
        result,calls=self.run_with(config={'browser_lease_script':'/fixture/browser-lease.py'})
        self.assertTrue(result);self.assertEqual(calls[0][1],'/fixture/browser-lease.py')
    def test_external_browser_profiles_are_left_to_their_owner(self):
        with patch.object(a.subprocess,'run') as run:
            for profile in ('chrome','user'):
                self.assertFalse(a.recover_browser(profile))
            self.assertFalse(a.recover_browser('openclaw',own_cdp=True))
            run.assert_not_called()

if __name__=='__main__':unittest.main()
