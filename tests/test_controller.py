import time
import unittest
from unittest.mock import patch
from royale_ai.controller import ADBController,ScreenLayout
from royale_ai.experiments import scenario
from royale_ai.state import Action

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.layout=ScreenLayout(540,960,[90,100,450,740],[[30+125*i,820,135+125*i,920] for i in range(4)])

    def test_dry_run_no_subprocess(self):
        ctrl=ADBController(layout=self.layout)
        with patch('subprocess.run') as run:
            row=ctrl.dispatch(scenario('giant'),Action(0,4,23.8))
            run.assert_not_called()
        self.assertEqual(row['status'],'dry_run')
        self.assertEqual(row['commands'][1][-2:],['170','576'])

    def test_stale_no_taps(self):
        ctrl=ADBController(layout=self.layout,execute=True)
        with patch('subprocess.run') as run:
            row=ctrl.dispatch(scenario('giant'),Action(0,4,23.8),time.monotonic()-2)
            run.assert_not_called()
        self.assertEqual(row['status'],'rejected')

    def test_low_confidence_rejected(self):
        ctrl=ADBController(layout=self.layout)
        obs=scenario('giant');obs.confidence=.1
        self.assertEqual(ctrl.dispatch(obs,Action(0,4,23.8))['status'],'rejected')

    def test_layout_invalid(self):
        with self.assertRaises(ValueError):
            ScreenLayout(540,960,[0,0,600,700],[[0,0,1,1]]*4)

if __name__=='__main__':unittest.main()
