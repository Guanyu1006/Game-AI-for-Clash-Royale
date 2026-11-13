"""Time alignment, hazard semantics, decoder parity and frozen-test contracts."""
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from arena_lab.model import PolicyAgent, PolicySpec, decode_actions, load_policy, save_policy, tensor_observation
    from arena_lab.timing import TimingConfig, TimingGRUPolicy, timing_action_log_prob_entropy, timing_losses
    from arena_lab.timing_training import (TimingBattleDataset, TimingTrainConfig, choose_threshold,
        average_precision, match_event_times, partition_development, train_timing)
    from arena_lab.training import sha256
    from arena_lab.timing_comparison import compare_timing


@unittest.skipIf(torch is None, "CPU Torch optional")
class TimingContracts(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        torch.manual_seed(27)
        self.spec = PolicySpec(global_dim=3, entity_dim=5, card_vocab_size=8, grid_h=4, grid_w=4,
                               hidden_dim=12, entity_hidden=8, card_embedding_dim=4)
        self.model = TimingGRUPolicy(self.spec).eval()
        self.parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-timing-tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def frame(self):
        return {"global_features": np.array([.1,.2,.3], dtype=np.float32),
                "entities": np.full((3,5), .2, dtype=np.float32), "entity_mask": np.array([1,1,0], dtype=bool),
                "hand_ids": np.array([1,2,3,4]), "legal_cards": np.array([1,1,1,1,1,0,0,0], dtype=bool),
                "legal_positions": np.ones((4,16), dtype=bool)}

    def dataset(self):
        rows = []
        for number, split in enumerate(["train"]*5+["validation"]*2+["test"]*2):
            steps = 8
            data = {key: np.stack([value.copy() for _ in range(steps)]) for key,value in self.frame().items()}
            data["global_features"][:,0] = np.linspace(0,1,steps) + number*.001
            cards = np.array([0,1,0,2,0,3,0,4], dtype=np.int64)
            ticks = np.arange(steps, dtype=np.int64)*10
            data.update(target_card=cards, target_position=np.where(cards>0, cards, 0), label_valid=np.ones(steps,dtype=bool),
                        position_valid=cards>0, ticks=ticks, observation_tick=ticks.copy(),
                        request_tick=np.where(cards>0,ticks,-1), confirmed_tick=np.where(cards>0,ticks,-1),
                        elapsed_ticks=np.r_[0,np.full(steps-1,10)].astype(np.int64), decision_ticks=np.full(steps,10,dtype=np.int64))
            path = self.root / f"game-{number}.npz"
            np.savez_compressed(path, **data)
            rows.append({"game_id": f"synthetic-{number}", "split": split, "steps": steps,
                         "path": path.name, "sha256": sha256(path)})
        path = self.root / "index.json"
        path.write_text(json.dumps({"schema_version":1,"policy_spec":asdict(self.spec),"games":rows,
                                    "tick_hz":20,"decision_ticks":10,"data_origin":"synthetic_unit_test"}))
        return path

    def test_event_decoder_does_not_split_probability_mass_across_cards(self):
        with torch.no_grad():
            self.model.event_head.weight.zero_()
            rate = -math.log(.4)/.5
            self.model.event_head.bias.fill_(math.log(math.expm1(rate)))
            self.model.card_head[-1].weight.zero_()
            self.model.card_head[-1].bias.zero_()
            out = self.model(tensor_observation(self.frame(),single=True))
        self.assertAlmostEqual(float(out["event_probability"].item()),.6,places=5)
        self.assertEqual(int(out["card_logits"].argmax(-1)),0)
        self.assertGreater(int(decode_actions(out)["card"]),0)

    def test_hazard_survival_composes_when_decision_horizon_changes(self):
        frame = self.frame()
        frame["decision_ticks"] = np.array(10)
        a = self.model(tensor_observation(frame,single=True))
        frame["decision_ticks"] = np.array(20)
        b = self.model(tensor_observation(frame,single=True))
        torch.testing.assert_close(a["hazard_rate"],b["hazard_rate"])
        torch.testing.assert_close(1-b["event_probability"],(1-a["event_probability"])**2)
        self.model.timing_config = replace(self.model.timing_config,hazard_temperature=.7,hazard_log_scale=.3)
        c = self.model(tensor_observation(frame,single=True))
        frame["decision_ticks"] = np.array(10)
        d = self.model(tensor_observation(frame,single=True))
        torch.testing.assert_close(1-c["event_probability"],(1-d["event_probability"])**2)

    def test_online_first_elapsed_and_sequence_decoder_match(self):
        frames = [self.frame() for _ in range(4)]
        obs = tensor_observation({key:np.stack([frame[key] for frame in frames])[None] for key in frames[0]})
        with torch.no_grad():
            out = self.model(obs)
            decoded = decode_actions(out)
            agent = PolicyAgent(self.model)
            for index,frame in enumerate(frames):
                action=agent.act(frame)
                self.assertEqual(action["card"],int(decoded["card"][0,index]))
                self.assertEqual(action["position"],int(decoded["position"][0,index]))
            torch.testing.assert_close(agent.hidden,out["hidden"],atol=1e-6,rtol=1e-5)
            explicit=dict(obs,elapsed_ticks=torch.tensor([[0,10,10,10]]))
            torch.testing.assert_close(out["card_logits"],self.model(explicit)["card_logits"])

    def test_future_request_and_confirmation_fields_do_not_enter_actor(self):
        frame=self.frame()
        original=self.model.act(frame)[0]
        frame.update(request_tick=np.array(50000),confirmed_tick=np.array(90000))
        self.assertEqual(original,self.model.act(frame)[0])

    def test_all_wait_masks_have_finite_gradients(self):
        frame=self.frame()
        frame["legal_cards"][1:]=False
        frame["legal_positions"][:]=False
        obs=tensor_observation(frame,single=True)
        out=self.model(obs)
        self.assertEqual(float(out["event_probability"].item()),0)
        decoded=decode_actions(out,deterministic=False,generator=torch.Generator().manual_seed(2))
        logp,entropy=timing_action_log_prob_entropy(out,decoded["card"],decoded["position"])
        self.assertEqual(float(logp),0)
        self.assertEqual(float(entropy),0)
        (-logp.mean()-.01*entropy.mean()).backward()
        for parameter in self.model.parameters():
            if parameter.grad is not None:
                self.assertTrue(torch.isfinite(parameter.grad).all())

    def test_joint_entropy_matches_enumerated_distribution_and_decode_logprob(self):
        out=self.model(tensor_observation(self.frame(),single=True))
        decoded=decode_actions(out)
        logp,entropy=timing_action_log_prob_entropy(out,decoded["card"],decoded["position"])
        torch.testing.assert_close(logp,decoded["log_probability"])
        card=out["card_logits"].softmax(-1)[0,0]
        position=out["position_logits"].softmax(-1)[0,0]
        distribution=torch.cat((card[:1],(card[1:5,None]*position).flatten()))
        exact=-(distribution*distribution.log()).sum()
        torch.testing.assert_close(entropy[0,0],exact)

    def test_checkpoint_preserves_timing_configuration_and_legacy_loader(self):
        self.model.timing_config=replace(self.model.timing_config,event_threshold=.31,hazard_temperature=.8)
        path=self.root/"timing.pt"
        saved=save_policy(self.model,path,metadata={"trained":True,"training_steps":5})
        other=save_policy(self.model,self.root/"different-name.pt",metadata={"trained":True,"training_steps":5})
        self.assertEqual(saved["sha256"],other["sha256"])
        loaded,_=load_policy(path,expected_sha256=saved["sha256"])
        self.assertIsInstance(loaded,TimingGRUPolicy)
        self.assertEqual(loaded.timing_config,self.model.timing_config)
        self.assertEqual(loaded.act(self.frame())[0],self.model.act(self.frame())[0])

    def test_future_label_rejected_and_development_groups_isolated(self):
        index=self.dataset()
        data=TimingBattleDataset(index)
        partition=partition_development(data,"fixed")
        groups=[set(ids) for ids in partition.values()]
        for i,group in enumerate(groups):
            for other in groups[i+1:]: self.assertFalse(group & other)
        row=data.splits["train"][0]
        arrays=data.load(row)
        arrays["request_tick"][1]+=1
        with self.assertRaisesRegex(ValueError,"Future request"):
            data._validate(arrays,row)

    def test_metrics_do_not_duplicate_events_and_ties_are_grouped(self):
        self.assertEqual(match_event_times(np.array([9,10,11]),np.array([10]),2),[-1])
        self.assertEqual(average_precision(np.array([.5,.5]),np.array([0,1])),.5)
        threshold,risk=choose_threshold(np.array([.2,.8]),np.array([False,True]))
        self.assertEqual(threshold,.8)
        self.assertEqual(risk,0)

    def test_real_optimizer_calibration_frozen_test_smoke(self):
        report=train_timing(self.dataset(),self.root/"run",TimingTrainConfig(epochs=1,batch_size=2,sequence=4,threads=1))
        self.assertGreater(report["checkpoint"]["metadata"]["training_steps"],0)
        self.assertFalse(report["checkpoint"]["metadata"]["human_expert_imitation"])
        self.assertEqual(report["test"]["labels"],16)
        frozen=json.loads((self.root/"run/frozen-before-test.json").read_text())
        self.assertEqual(frozen["checkpoint_sha256"],report["checkpoint"]["sha256"])
        self.assertEqual(report["calibration_parameters"],frozen["calibration"])
        self.assertEqual(report["calibration"]["split"],"calibration")
        self.assertEqual(len(report["development_splits"]["test"]),2)

    def test_joint_baseline_and_actual_epoch_resume_preserve_weights(self):
        index=self.dataset()
        config=TimingTrainConfig(epochs=2,batch_size=2,sequence=4,threads=1,model_kind="joint")
        interrupted=train_timing(index,self.root/"resumed",config,stop_after_epoch=1)
        self.assertFalse(interrupted["test_evaluated"])
        self.assertFalse((self.root/"resumed/frozen-before-test.json").exists())
        resumed=train_timing(index,self.root/"resumed",config,resume=True)
        uninterrupted=train_timing(index,self.root/"full",config)
        left,_=load_policy(resumed["checkpoint"]["path"])
        right,_=load_policy(uninterrupted["checkpoint"]["path"])
        for key,value in left.state_dict().items():
            torch.testing.assert_close(value,right.state_dict()[key],atol=0,rtol=0)
        self.assertEqual(resumed["test"]["card_nll"],uninterrupted["test"]["card_nll"])
        with self.assertRaisesRegex(FileExistsError,"Completed runs"):
            train_timing(index,self.root/"resumed",config,resume=True)

    def test_paired_comparison_checks_frozen_split_and_same_observations(self):
        index=self.dataset()
        candidate_config=TimingTrainConfig(epochs=1,batch_size=2,sequence=4,threads=1)
        reference_config=replace(candidate_config,model_kind="joint")
        partition=partition_development(TimingBattleDataset(index),candidate_config.split_salt)
        plan={"dataset_index_sha256":sha256(index),"splits":partition,
              "timing":asdict(candidate_config),"joint_baseline":asdict(reference_config),
              "uncertainty":{"seed":17,"bootstrap_repetitions":25,"unit":"whole battle"}}
        plan_path=self.root/"plan.json"
        plan_path.write_text(json.dumps(plan))
        train_timing(index,self.root/"candidate",candidate_config)
        train_timing(index,self.root/"reference",reference_config)
        compared=compare_timing(plan_path,self.root/"candidate",self.root/"reference",self.root/"compare.json")
        self.assertEqual(compared["battles"],2)
        self.assertEqual(compared["labels"],16)
        path=self.root/"reference/test-predictions.npz"
        with np.load(path,allow_pickle=False) as archive:
            records={key:archive[key] for key in archive.files}
        records["ticks"][0]+=1
        np.savez_compressed(path,**records)
        with self.assertRaisesRegex(ValueError,"identical held-out"):
            compare_timing(plan_path,self.root/"candidate",self.root/"reference",self.root/"bad.json")
        records["ticks"][0]-=1
        np.savez_compressed(path,**records)
        # Two mutually consistent files can still carry the wrong labels or
        # battle grouping. The immutable source, not agreement, is authoritative.
        originals={}
        for name in ("candidate","reference"):
            with np.load(self.root/name/"test-predictions.npz",allow_pickle=False) as archive:
                originals[name]={key:archive[key] for key in archive.files}
        for key in ("position","eligible","trajectory"):
            for name in ("candidate","reference"):
                tampered={field:value.copy() for field,value in originals[name].items()}
                tampered[key][0]=1-int(tampered[key][0])
                np.savez_compressed(self.root/name/"test-predictions.npz",**tampered)
            with self.assertRaisesRegex(ValueError,"frozen original source: "+key):
                compare_timing(plan_path,self.root/"candidate",self.root/"reference",self.root/f"bad-{key}.json")
        for name in ("candidate","reference"):
            np.savez_compressed(self.root/name/"test-predictions.npz",**originals[name])
        frozen_path=self.root/"candidate/frozen-before-test.json"
        frozen=json.loads(frozen_path.read_text())
        frozen["tolerance_ticks"]+=10
        frozen_path.write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError,"pre-test freeze"):
            compare_timing(plan_path,self.root/"candidate",self.root/"reference",self.root/"bad-freeze.json")


if __name__ == "__main__":
    unittest.main()
