"""Boundary and evidence-denominator tests, not native rollout claims."""
from copy import deepcopy
import unittest
from royale_phase3.actions import DECK,PublicTeacher,grid_to_native,native_to_grid,position_mask,validate
from royale_phase3.data import split_for

class Actions(unittest.TestCase):
    def actor(self):return {'native_owner':0,'tick':90,'own_elixir_raw':40000,'hand':[{'slot':0,'card':26000003,'cost':5},{'slot':1,'card':28000000,'cost':4}]}
    def test_all_grid_centers_roundtrip(self):
        for cell in range(576):self.assertEqual(native_to_grid(*grid_to_native(cell)),cell)
    def test_invalid_grid_and_types(self):
        for cell in (-1,576,True,1.0):
            with self.assertRaises(ValueError):grid_to_native(cell)
    def test_affordability_not_legality(self):
        actor=self.actor()
        with self.assertRaises(ValueError):validate(actor,{'kind':'play_card','slot':0,'x':3500,'y':11000})
        self.assertEqual(validate(actor,{'kind':'play_card','slot':1,'x':0,'y':31999})['card'],28000000)
    def test_public_own_half_rule(self):
        actor=self.actor();actor['own_elixir_raw']=60000
        with self.assertRaises(ValueError):validate(actor,{'kind':'play_card','slot':0,'x':3500,'y':21000})
        self.assertEqual(sum(position_mask(26000003,0)),288)
        self.assertEqual(sum(position_mask(28000000,1)),576)
    def test_teacher_saves_for_expensive_card(self):
        actor=self.actor();teacher=PublicTeacher('cycle',1)
        self.assertEqual(teacher.decide(actor),{'kind':'wait'})
        actor['own_elixir_raw']=60000
        self.assertEqual(teacher.decide(actor)['card'],26000003)
    def test_random_state_reproducible(self):
        a=PublicTeacher('random',42);b=PublicTeacher('random',42);actor=self.actor()
        for tick in range(90,1000,10):
            actor['tick']=tick;self.assertEqual(a.decide(deepcopy(actor)),b.decide(deepcopy(actor)))
        self.assertEqual(a.rng.getstate(),b.rng.getstate())
    def test_frozen50_splits(self):
        from collections import Counter
        self.assertEqual(Counter(split_for(i) for i in range(50)),{'train':30,'validation':10,'test':10})

if __name__=='__main__':unittest.main()
