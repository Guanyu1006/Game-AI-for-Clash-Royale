"""Visual unknown masks, coordinate mapping and no oracle contamination."""
from copy import deepcopy
import unittest
from unittest.mock import patch
import numpy as np
try:
    import torch
except ImportError:
    torch = None


@unittest.skipIf(torch is None, "Optional Torch unavailable")
class PixelPolicyTests(unittest.TestCase):
    def setUp(self):
        from arena_lab.pixel_policy import NATIVE_NAMES, PixelProjection
        self.names = list(NATIVE_NAMES.values())
        self.identity = {"cards": self.names, "unit_types": self.names[:6],
                         "binary_sha256": "f"*64,
                         "obs_spec": {"global_dim": 40, "entity_dim": 40, "max_entities": 8}}
        self.catalogue = [{"name": name, "elixir": 3, "card_kind": "SPELL" if i >= 6 else
                           "BUILDING" if i == 5 else "TROOP"} for i, name in enumerate(self.names)]
        from arena_lab.visual_geometry import SCHEMA, object_sha256
        source = {"fixture": "Known public obstacle at point 100; no live state"}
        self.geometry = {"schema": SCHEMA, "grid_shape": [32, 18], "engine_binary_sha256": "f"*64,
                         "source": source, "source_sha256": object_sha256(source),
                         "cards": {str(native): {"name": name, "candidate_mask":
                             [point != 100 and (index >= 6 or point < 270) for point in range(576)]}
                             for index, (native, name) in enumerate(NATIVE_NAMES.items())}}
        self.projection = PixelProjection(self.identity, self.catalogue, geometry=self.geometry)

    def source(self):
        obs = {"global_features": np.ones(40, np.float32), "entities": np.zeros((8, 40), np.float32),
               "entity_mask": np.array([True]+[False]*7), "hand_ids": np.array([1, 2, 3, 4]),
               "legal_cards": np.ones(8, bool), "legal_positions": np.ones((4, 576), bool)}
        obs["global_features"][4] = .875
        obs["entities"][0, :6] = [.25, .3, .8, 1, 1, 0]
        obs["entities"][0, 22] = 1  # Knight unit
        return obs

    def test_unknown_values_never_enter_actor_and_are_distinct_from_known_zero(self):
        source = self.source()
        original = self.projection.simulator(source)
        source["global_features"][:4] = 99
        source["global_features"][5:16] = 99
        source["entities"][0, [2, 3, 10, 11, 12, 13, 14, 15]] = 99
        changed = self.projection.simulator(source)
        for key in original:
            np.testing.assert_array_equal(original[key], changed[key])
        self.assertEqual(original["global_features"][40+4], 1)  # observed resource
        self.assertEqual(original["global_features"][40], 0)  # absent clock
        self.assertEqual(original["entities"][0, 40+2], 0)  # absent HP
        self.assertEqual(original["entities"][0, 40+3], 0)  # uncalibrated team
        self.assertFalse(original["legal_cards"][5:].any())

    def test_real_pixel_mapping_matches_projected_contract(self):
        pixel = {"source": "screenshot_pixels_only", "elixir": {"lower_bound": 8},
                 "hand": [{"slot": i, "card_id": native, "known": True, "visually_ready": True}
                          for i, native in enumerate([26000000, 26000001, 26000003, 26000014])],
                 "detections": [{"label": "knight", "xy_native_estimate": [4500, 22400]}]}
        projected = self.projection.simulator(self.source())
        actual = self.projection.pixels(pixel)
        for key in projected:
            np.testing.assert_allclose(projected[key], actual[key], atol=1e-7)
        pixel.update(opponent_hand=["hidden"], tower_hp=[9999], native_state={"tick": 20000})
        for key in actual:
            np.testing.assert_array_equal(actual[key], self.projection.pixels(pixel)[key])
        pixel["scene_ready"] = False
        blocked = self.projection.pixels(pixel)
        self.assertEqual(blocked["legal_cards"].tolist(), [True]+[False]*7)
        self.assertFalse(blocked["entity_mask"].any())
        self.assertFalse(blocked["global_features"].any())

    def test_unknown_hand_or_elixir_forces_wait_and_does_not_invent_cards(self):
        pixel = {"source": "screenshot_pixels_only", "elixir": {"lower_bound": None},
                 "hand": [{"slot": 0, "card_id": 26000000, "known": True, "visually_ready": True}],
                 "detections": []}
        result = self.projection.pixels(pixel)
        self.assertEqual(result["legal_cards"].tolist(), [True]+[False]*7)
        pixel["elixir"]["lower_bound"] = 10
        pixel["hand"][0]["known"] = False
        self.assertEqual(self.projection.pixels(pixel)["hand_ids"].tolist(), [0, 0, 0, 0])
        with self.assertRaisesRegex(ValueError, "screenshot-derived"):
            self.projection.pixels(dict(pixel, source="native_oracle"))

    def test_unknown_detected_class_retains_position_with_type_unknown(self):
        pixel = {"source": "screenshot_pixels_only", "elixir": {}, "hand": [],
                 "detections": [{"label": "unrecognized_building", "xy_native_estimate": [9000, 20000]}]}
        result = self.projection.pixels(pixel)
        self.assertEqual(int(result["entity_mask"].sum()), 1)
        np.testing.assert_allclose(result["entities"][0, :2], [.5, .375])
        self.assertTrue((result["entities"][0, 42:] == 0).all())

    def test_static_obstacle_survives_both_inputs_and_ignores_live_oracle_mask(self):
        source = self.source()
        target = self.projection.simulator(source)
        self.assertFalse(target["legal_positions"][:, 100].any())
        self.assertTrue(target["legal_positions"][:, 101].all())
        source["legal_positions"][:] = False  # e.g. an unobservable opening lockout
        source["legal_cards"][:] = False
        for key, value in target.items():
            np.testing.assert_array_equal(value, self.projection.simulator(source)[key])

    def test_geometry_binding_rejects_drift_and_can_reconstruct_without_asset(self):
        from arena_lab.pixel_policy import PixelProjection
        identity = self.projection.identity()
        embedded = identity["visual_projection"]
        with patch("arena_lab.visual_geometry.ASSET") as asset:
            asset.read_text.side_effect = AssertionError("Deployment must use trained geometry")
            restored = PixelProjection(embedded["base_identity"], embedded["catalogue"], embedded["geometry"])
            self.assertEqual(restored.identity(), identity)
        wrong_engine = deepcopy(self.identity)
        wrong_engine["binary_sha256"] = "e"*64
        with self.assertRaisesRegex(ValueError, "engine binary SHA"):
            PixelProjection(wrong_engine, self.catalogue, self.geometry)
        changed_source = deepcopy(self.geometry)
        changed_source["source"]["fixture"] = "modified"
        with self.assertRaisesRegex(ValueError, "source checksum"):
            PixelProjection(self.identity, self.catalogue, changed_source)
        bad_mask = deepcopy(self.geometry)
        bad_mask["cards"]["26000000"]["candidate_mask"][0] = 1
        with self.assertRaisesRegex(ValueError, "mask malformed"):
            PixelProjection(self.identity, self.catalogue, bad_mask)

    def test_deployment_rejects_untrained_and_wrong_model_family(self):
        from arena_lab.model import EntityGRUPolicy, PolicySpec
        from arena_lab.pixel_policy import PixelModelController, VERSION
        from arena_lab.timing import TimingGRUPolicy
        spec = PolicySpec(80, 80, len(self.names)+1, observation_version=VERSION)
        for model, metadata in ((TimingGRUPolicy(spec), {"trained": False, "training_steps": 0}),
                                (EntityGRUPolicy(spec), {"trained": True, "training_steps": 50})):
            with patch("arena_lab.pixel_policy.load_policy", return_value=(model, metadata)):
                with self.assertRaisesRegex(ValueError, "actually trained TimingGRUPolicy"):
                    PixelModelController("unused-test-path")

    def test_deployment_uses_embedded_geometry_and_rejects_mask_checksum_drift(self):
        from arena_lab.model import PolicySpec
        from arena_lab.pixel_policy import PixelModelController, VERSION
        from arena_lab.timing import TimingGRUPolicy
        model = TimingGRUPolicy(PolicySpec(80, 80, len(self.names)+1, observation_version=VERSION))
        metadata = {"trained": True, "training_steps": 5, "environment_identity": self.projection.identity()}
        with patch("arena_lab.pixel_policy.load_policy", return_value=(model, metadata)), \
                patch("arena_lab.visual_geometry.ASSET") as asset:
            asset.read_text.side_effect = AssertionError("Disk geometry must not override checkpoint")
            controller = PixelModelController("unused-test-path")
            self.assertEqual(controller.projection.identity(), metadata["environment_identity"])
            metadata["environment_identity"]["visual_projection"]["geometry"]["cards"]["26000000"]["candidate_mask"][100] = True
            with self.assertRaisesRegex(ValueError, "checksum differs"):
                PixelModelController("unused-test-path")


if __name__ == "__main__":
    unittest.main()
