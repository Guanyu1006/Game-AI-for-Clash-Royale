"""Behavioral tests for the optional pinned two-seat simulator targets."""
from pathlib import Path
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = (ROOT / ".cache/arena-lab/python/royalesim").is_dir()
if AVAILABLE:
    from arena_lab.environment import ArenaConfig, ArenaEnv, DEFAULT_DECK
    import msgspec
    from royalegym.protocol import EntityState, STATUS_INVISIBLE


@unittest.skipUnless(AVAILABLE, "optional arena target not installed; run arena_lab.bootstrap install")
class RoyaleEnvironmentTests(unittest.TestCase):
    def test_joint_native_feedback_distinguishes_queued_and_executed(self):
        env = ArenaEnv(ArenaConfig(shuffle=0, command_delay_ticks=20))
        obs, _ = env.reset(seed=10)
        _, _, _, _, early = env.step({0:{"card":1,"position":219},1:0})
        self.assertEqual(early["deploy_results"][0]["reason"], "TOO_EARLY")
        self.assertEqual(early["executed_total"],0)
        env.step({0:0,1:0},ticks=80)
        _, _, _, _, queued = env.step({0:{"card":1,"position":219},1:{"card":1,"position":219}},ticks=1)
        self.assertEqual(queued["accepted"],2)
        self.assertEqual(queued["executed_total"],0)
        _, _, _, _, executed = env.step({0:0,1:0},ticks=25)
        self.assertEqual({r[1] for r in executed["commands_run"]}, {0,1})
        self.assertEqual(executed["executed_total"],2)
        self.assertTrue(all(r[-1]==0 for r in executed["commands_run"]))

    def test_private_enemy_state_and_invisible_occupancy_do_not_change_actor(self):
        env = ArenaEnv(); env.reset(seed=22)
        env.step({0:0,1:0},ticks=100)
        state = env.state
        own = env.observe(0,state)
        self.assertTrue(own["legal_positions"].any())
        enemy = msgspec.structs.replace(state.players[1],elixir_milli=9999,
                    hand=list(reversed(state.players[1].hand)),next_card=100,
                    deck=list(reversed(state.players[1].deck)),pending=[[0,1,0,0,900,3]],
                    pending_cost=3,abilities=[[1,0,1,69,0]])
        invisible = EntityState(uid=900,team=1,kind=1,card_id=95,tower_slot=-1,
                                x=100000,y=100000,hp=1000,max_hp=1000,radius=9000,
                                flying=False,deploy_ticks=0,status_flags=STATUS_INVISIBLE,
                                footprint=(80000,80000,120000,120000),unit_type=0)
        altered = msgspec.structs.replace(state,players=[state.players[0],enemy],
                                          entities=[*state.entities,invisible])
        changed = env.observe(0,altered)
        for key in own:
            np.testing.assert_array_equal(own[key],changed[key],err_msg=key)

    def test_level_forms_initial_order_and_own_ability_information(self):
        deck=["ArcherQueen","Knight",*DEFAULT_DECK[2:]]
        env=ArenaEnv(ArenaConfig(decks=[deck,deck],shuffle=0,card_level=16,tower_level=16,
                                 forms=[[0,1,0,0,0,0,0,0]]*2))
        obs,info=env.reset(seed=3,elixir_milli=[7000,4000])
        self.assertEqual(info["episode_setup"]["initial_hands"][0],env._decks([deck,deck])[0][:4])
        self.assertAlmostEqual(float(obs[0]["global_features"][4]),.7,places=6)
        # ability known/available/spent/cost/cooldown begin after the original 20 globals
        self.assertEqual(obs[0]["global_features"][20],1)
        self.assertAlmostEqual(float(obs[0]["global_features"][23]),.1,places=6)
        self.assertEqual(obs[0]["global_features"].shape,(env.obs_spec["global_dim"],))
        self.assertEqual(obs[0]["entities"].shape[-1],env.obs_spec["entity_dim"])

    def test_seed_and_joint_actions_repeat_exactly(self):
        env=ArenaEnv(ArenaConfig(command_delay_ticks=20))
        histories=[]
        for repeat in range(2):
            obs,_=env.reset(seed=73); hashes=[]
            for step in range(30):
                actions={}
                for seat in (0,1):
                    cards=np.flatnonzero(obs[seat]["legal_cards"])[1:]
                    card=int(cards[0]) if len(cards) and step%3==0 else 0
                    pos=int(np.flatnonzero(obs[seat]["legal_positions"][card-1])[0]) if 1<=card<=4 else 0
                    actions[seat]={"card":card,"position":pos}
                obs,_,_,_,info=env.step(actions);hashes.append(info["state_hash"])
            histories.append(hashes)
        self.assertEqual(histories[0],histories[1])

    def test_limit_is_truncation_and_unknown_deck_is_transactionally_refused(self):
        env=ArenaEnv(ArenaConfig(max_ticks=7));env.reset(seed=8)
        before=env.engine.state_hash()
        with self.assertRaisesRegex(ValueError,"Unsupported card"):
            env.reset(decks=[["unknown",*DEFAULT_DECK[1:]],DEFAULT_DECK])
        self.assertEqual(env.engine.state_hash(),before)
        _,rewards,terminated,truncated,info=env.step({0:0,1:0})
        self.assertFalse(terminated);self.assertTrue(truncated)
        self.assertEqual(info["tick"],7);self.assertEqual(info["winner"],-1)
        self.assertEqual(rewards,{0:0.,1:0.})
        with self.assertRaisesRegex(RuntimeError,"ended"):
            env.step({0:0,1:0})

    def test_replay_does_not_invent_a_missing_hand_card(self):
        env=ArenaEnv(ArenaConfig(shuffle=0));env.reset()
        with self.assertRaisesRegex(ValueError,"absent"):
            env.advance_commands([{"team":0,"card_id":"Fireball","x":90000,"y":180000}])


CRFORGE_AVAILABLE = AVAILABLE and (ROOT / ".cache/arena-lab/crforge-data/tables/16.635.8").is_dir()


@unittest.skipUnless(CRFORGE_AVAILABLE,"optional pinned crforge JDK/build/tables not installed")
class CrforgeEnvironmentTests(unittest.TestCase):
    def test_seed_repeats_native_buffers_and_unknown_enemy_hp_is_preserved(self):
        deck=["DarkPrince","SkeletonWarriors","ArcherQueen","Giant","Musketeer","Fireball","Arrows","Skeletons"]
        env=ArenaEnv(ArenaConfig(backend="crforge",decks=[deck,deck],max_entities=200))
        try:
            histories=[];saw_unknown_enemy=False
            for repeat in range(2):
                obs,_=env.reset(seed=71);hashes=[]
                for step in range(30):
                    actions={}
                    for seat in (0,1):
                        card=0;pos=0
                        if step==10:
                            for slot,cid in enumerate(obs[seat]["hand_ids"]):
                                if cid and env.cards[int(cid)-1]["name"] in {"DarkPrince","SkeletonWarriors","ArcherQueen"} and obs[seat]["legal_cards"][slot+1]:
                                    card=slot+1;pos=int(np.flatnonzero(obs[seat]["legal_positions"][slot])[0]);break
                        actions[seat]={"card":card,"position":pos}
                    obs,_,_,_,info=env.step(actions);hashes.append(info["state_hash"])
                    for seat in (0,1):
                        units=obs[seat]["entities"][obs[seat]["entity_mask"]]
                        enemy=units[(units[:,3]<.5)&(units[:,4]>.5)]
                        for unit in enemy:
                            if unit[21]==0:  # hp_known is the first crforge extra field
                                saw_unknown_enemy=True
                                self.assertEqual(unit[2],0)
                histories.append(hashes)
            self.assertEqual(histories[0],histories[1])
            self.assertTrue(saw_unknown_enemy,"Need a real enemy unit whose screen has no health bar")
        finally:
            env.close()

    def test_two_seat_frames_ownership_and_native_consumption(self):
        env=ArenaEnv(ArenaConfig(backend="crforge",max_entities=200))
        try:
            obs,_=env.reset(seed=13)
            for seat in (0,1):
                units=obs[seat]["entities"][obs[seat]["entity_mask"]]
                self.assertEqual(int((units[:,3]>.5).sum()),3)
                own_king=units[(units[:,3]>.5)&(units[:,6]>.5)][0]
                self.assertLess(own_king[1],.25)
            for step in range(14):
                acts={}
                for seat in (0,1):
                    cards=np.flatnonzero(obs[seat]["legal_cards"])[1:5]
                    card=int(cards[0]) if len(cards) and step==10 else 0
                    pos=int(np.flatnonzero(obs[seat]["legal_positions"][card-1])[0]) if card else 0
                    acts[seat]={"card":card,"position":pos}
                obs,_,_,_,info=env.step(acts)
            self.assertEqual(info["accepted_total"],2)
            self.assertEqual(info["executed_total"],2)
            self.assertEqual(info["refused_total"],0)
            self.assertEqual(env.identity()["data_version"],"16.635.8")
            self.assertEqual(env.identity()["observation_version"],"arena-crforge-public-v1")
        finally:
            env.close()

    def test_level16_evolution_champion_and_unknown_hand_control(self):
        deck=["ArcherQueen","Knight",*DEFAULT_DECK[2:]]
        env=ArenaEnv(ArenaConfig(backend="crforge",decks=[deck,deck],card_level=16,
                                 tower_level=16,forms=[[0,1,0,0,0,0,0,0]]*2,max_ticks=30))
        try:
            obs,info=env.reset(seed=23)
            self.assertEqual(info["episode_setup"]["levels"],[[16]*8]*2)
            self.assertEqual(info["episode_setup"]["slots"][0][1],1)
            with self.assertRaisesRegex(ValueError,"initial-hand"):
                env.reset(shuffle=0)
            for _ in range(3):
                obs,reward,term,trunc,info=env.step({0:0,1:0})
            self.assertTrue(trunc);self.assertFalse(term)
            self.assertEqual(info["winner"],-1)
            self.assertTrue(all(i["fault"]=="none" for i in info["native_seat_info"]))
        finally:
            env.close()


if __name__=="__main__":
    unittest.main()
