"""Portable, offline checks against included source and real derived samples."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")


def main():
    from VERIFY_PACKAGE import verify
    integrity = verify()
    import numpy as np
    from royale_phase2.learning import (actor_features, forward, decode_policy_output,
                                        action_from_index)
    meta = json.loads((ROOT / "models/friend_bc/checkpoint.json").read_text(encoding="utf-8"))
    with np.load(ROOT / "models/friend_bc/weights.npz", allow_pickle=False) as archive:
        parameters = {key: archive[key] for key in archive.files}
    samples = json.loads((ROOT / "examples/friend_native_samples.json").read_text(encoding="utf-8"))
    predictions = []
    for sample in samples["samples"]:
        actor = sample["actor"]
        x = actor_features(actor, meta["feature_configuration"])[None, :]
        mask = np.asarray([sample["recorded_mask"]], dtype=bool)
        _, probability, xy = forward(parameters, x, mask)
        index, point = decode_policy_output(probability, xy, mask,
                                            play_bias=meta.get("inference_play_bias", 0.0))
        action = action_from_index(actor, int(index[0]), point[0])
        if action != sample["expected_deployment_decoding"]:
            raise AssertionError("Numeric prediction differs for " + sample["sample_id"])
        predictions.append({"sample_id": sample["sample_id"], "action": action})
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTests(loader.discover(str(ROOT / "tests"), pattern="test_core.py"))
    from tests_phase3.test_evaluation import EvaluationContracts
    # The seventh upstream test intentionally audits proprietary catalog evidence,
    # which is not distributed. Its omission is explicit, never called a pass.
    for name in (
        "test_bias_changes_choice_but_never_allows_masked_card",
        "test_coordinate_rounding_matches_historical_scalar_dtype",
        "test_decoder_rejects_nonfinite_bias_and_invalid_mask",
        "test_diagnostic_single_and_batch_actions_identical",
        "test_event_matching_is_one_to_one_and_has_frozen_tolerance",
        "test_random_legal_reproduces_state_and_digest",
    ):
        suite.addTest(EvaluationContracts(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"status": "pass" if result.wasSuccessful() else "fail",
              "integrity": integrity, "python": sys.version, "numpy": np.__version__,
              "unit_tests_run": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "real_sample_predictions": predictions,
              "scope": "Offline source/contracts and saved-model numeric decoding; "
                       "recorded masks are reused, native legality/runtime is not rerun.",
              "excluded_test": "test_forged_hard_e_mask_rejected_before_prediction "
                               "requires separately obtained catalog/evidence."}
    out = ROOT / "friend_runs"
    out.mkdir(exist_ok=True)
    (out / "smoke_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
