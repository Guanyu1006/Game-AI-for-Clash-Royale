import importlib.util
import unittest
from royale_ai.paths import inside

@unittest.skipUnless(importlib.util.find_spec('cv2'),'optional OpenCV not installed')
class VisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from royale_ai.synthetic import build_demo
        cls.frame,cls.config=build_demo('outputs/test_vision')

    def test_pixels_to_action(self):
        from royale_ai.perception import ScreenObserver
        from royale_ai.policy import LookaheadPolicy
        observer=ScreenObserver(self.config)
        obs=observer.observe(self.frame,30)
        self.assertEqual(obs.hand,['mini_pekka','musketeer','arrows','knight'])
        self.assertEqual(obs.elixir,7)
        self.assertEqual(len(obs.units),3)
        self.assertEqual(obs.towers[0].hp,2500)
        self.assertGreater(obs.confidence,.9)
        self.assertEqual(LookaheadPolicy().decide(obs).card,'mini_pekka')
        obs2=observer.observe(self.frame,31)
        self.assertEqual([u.id for u in obs.units],[u.id for u in obs2.units])
        self.assertEqual(obs2.events,[])

    def test_anchor_absent(self):
        from royale_ai.perception import ScreenObserver,PerceptionError,read_image,write_image
        image=read_image(self.frame);image[20:55,205:335]=0
        path=inside('outputs/test_vision/no_anchor.png');write_image(path,image)
        with self.assertRaises(PerceptionError):ScreenObserver(self.config).observe(path,0)

    def test_unknown_hand(self):
        from royale_ai.perception import ScreenObserver,PerceptionError,read_image,write_image
        image=read_image(self.frame);image[820:920,30:135]=0
        path=inside('outputs/test_vision/unknown_hand.png');write_image(path,image)
        with self.assertRaises(PerceptionError):ScreenObserver(self.config).observe(path,0)

if __name__=='__main__':unittest.main()
