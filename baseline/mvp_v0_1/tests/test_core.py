import copy
import math
import unittest
from royale_ai.belief import OpponentBelief,regen_between
from royale_ai.cards import CARDS,DEFAULT_DECK
from royale_ai.experiments import scenario
from royale_ai.paths import inside
from royale_ai.policy import LookaheadPolicy,candidates
from royale_ai.simulator import Arena
from royale_ai.state import Action,Entity,Observation,legal_action

class CoreTests(unittest.TestCase):
    def test_deck_cycle_and_elixir(self):
        a=Arena(shuffle=False)
        first=a.players[0].hand[0]
        a.players[0].elixir=10
        self.assertTrue(a.play(0,Action(0,4,18)))
        self.assertEqual(a.players[0].elixir,10-CARDS[first].cost)
        self.assertNotIn(first,a.players[0].hand)
        for _ in range(4):
            a.players[0].elixir=10
            a.play(0,Action(0,4,18))
        self.assertIn(first,a.players[0].hand)

    def test_illegal_actions_do_not_mutate(self):
        a=Arena(shuffle=False)
        before=a.clone().__dict__
        for action in [Action(0,4,10),Action(0,-1,18),Action(0,float('nan'),18),Action(8,4,18)]:
            self.assertFalse(a.play(0,action))
            self.assertEqual(a.__dict__,before)
        a.players[0].elixir=0
        self.assertFalse(a.play(0,Action(0,4,18)))

    def test_regen_boundary(self):
        self.assertAlmostEqual(regen_between(119,121),3/2.8)
        self.assertAlmostEqual(regen_between(179,181),5/2.8)

    def test_confirmed_events_and_dedup(self):
        b=OpponentBelief(low=5,high=5)
        event={"id":"1","kind":"play","card":"giant","team":1,"time":0}
        b.update(0,[event,event])
        self.assertEqual((b.low,b.high,b.played_count),(0,0,1))
        self.assertNotIn('giant',b.candidates())
        for i,cid in enumerate(['knight','archers','minions','musketeer']):
            b.update(20*(i+1),[{"id":str(i+2),"kind":"play","card":cid,"team":1,"time":20*(i+1)}])
        self.assertIn('giant',b.candidates())

    def test_inferred_tracks_not_confirmed_plays(self):
        b=OpponentBelief()
        b.update(0,[{"id":"track_1","kind":"seen","card":"hog_rider","team":1,"time":0}])
        self.assertEqual(b.played_count,0)
        self.assertIn('hog_rider',b.candidates())

    def test_midmatch_belief_remains_uncertain(self):
        obs=scenario('giant')
        p=LookaheadPolicy()
        p.decide(obs)
        self.assertEqual((p.belief.low,p.belief.high),(0,10))

    def test_screen_belief_not_exact_after_unseen_spending(self):
        p=LookaheadPolicy()
        obs=scenario('save');obs.source='screen'
        p.decide(obs)
        obs.time=130
        p.decide(obs)
        self.assertEqual((p.belief.low,p.belief.high),(0,10))

    def test_serialization_and_hidden_hand(self):
        obs=Arena().observation()
        self.assertEqual(Observation.from_dict(obs.to_dict()),obs)
        self.assertNotIn('opponent_hand',obs.to_dict())
        obs.hand[0]='unknown'
        with self.assertRaises(ValueError): obs.validate()

    def test_perspective_rotation(self):
        a=Arena(shuffle=False)
        a.play(1,Action(0,4,18))
        obs=a.observation(1)
        self.assertEqual(obs.units[0].team,0)
        self.assertEqual((obs.units[0].x,obs.units[0].y),(4,18))
        self.assertEqual(obs.events[0]['team'],0)

    def test_clone_deterministic(self):
        a=Arena(seed=5);b=a.clone()
        self.assertTrue(a.play(0,Action(0,4,18)))
        b.play(0,Action(0,4,18))
        a.advance(12);b.advance(12)
        self.assertEqual(a.observation().to_dict(),b.observation().to_dict())

    def test_rollout_enemy_cycle_has_no_immediate_duplicate(self):
        obs=scenario('giant')
        a=Arena.from_observation(obs,['giant','knight','archers','minions'],10)
        played=a.players[1].hand[0]
        self.assertTrue(a.play(1,Action(0,4,18)))
        self.assertNotIn(played,a.players[1].hand)

    def test_rollout_stops_at_regulation_end(self):
        obs=scenario('giant');obs.time=178
        a=Arena.from_observation(obs,DEFAULT_DECK,0)
        a.advance(8)
        self.assertEqual(a.time,180)
        self.assertTrue(a.done)

    def test_spells_remove_killed_units(self):
        a=Arena(shuffle=False)
        a.players[0].hand[0]='arrows'
        a.units=[Entity(1,'minions',1,4,23,210)]
        a.play(0,Action(0,4,23))
        self.assertEqual(len(a.units),0)

    def test_air_targeting(self):
        a=Arena(shuffle=False)
        a.units=[Entity(1,'knight',0,9,16,1450),Entity(2,'minions',1,9,16,210)]
        target=a._target(a.units[0])
        self.assertNotEqual(getattr(target,'card',None),'minions')
        a.units[0].card='musketeer'
        self.assertEqual(a._target(a.units[0]).id,2)

    def test_dead_king_terminates_and_activates_on_damage(self):
        a=Arena(shuffle=False)
        a.players[0].hand[0]='fireball'
        a.players[0].elixir=10
        king=next(t for t in a.towers if t.team==1 and t.king)
        king.hp=100
        a.play(0,Action(0,king.x,king.y))
        self.assertTrue(king.active)
        self.assertTrue(a.done)
        self.assertEqual(a.winner(),0)

    def test_tactics(self):
        d=LookaheadPolicy().decide(scenario('giant'))
        self.assertEqual(d.card,'mini_pekka')
        # Ground troops can tank minions while a tower shoots them; quality is
        # measured by damage avoided rather than requiring one exact card label.
        obs=scenario('air')
        d=LookaheadPolicy().decide(obs)
        defended=Arena.from_observation(obs,DEFAULT_DECK,0)
        waiting=defended.clone()
        defended.play(0,d.action)
        defended.advance(8);waiting.advance(8)
        self.assertGreater(sum(t.hp for t in defended.towers if t.team==0),
                           sum(t.hp for t in waiting.towers if t.team==0))
        d=LookaheadPolicy().decide(scenario('finish'))
        self.assertIn(d.card,['arrows','fireball'])
        self.assertEqual(d.action.y,5.5)
        self.assertTrue(LookaheadPolicy().decide(scenario('save')).action.is_wait)

    def test_low_confidence_waits(self):
        obs=scenario('giant');obs.confidence=.5
        self.assertTrue(LookaheadPolicy().decide(obs).action.is_wait)

    def test_candidates_legal_and_unique(self):
        obs=scenario('air')
        actions=candidates(obs)
        self.assertEqual(len(actions),len(set(actions)))
        self.assertTrue(all(legal_action(obs,a) for a in actions))

    def test_output_cannot_leave_workspace(self):
        with self.assertRaises(ValueError): inside('../escape.json')

if __name__=='__main__':unittest.main()
