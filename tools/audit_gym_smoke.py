"""Probe the actual MSU-AI gym API without interpreting README claims as evidence."""
import importlib.metadata
import json
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[1]
result = {'candidate': 'MSU-AI/clash-royale-gym', 'commit': '1cd23be16e10b25c5a5a0889626a863092f32615',
          'packages': {}, 'claims_verified': False}
for package in ['clash_royale', 'gymnasium', 'pygame', 'numpy']:
    try:
        result['packages'][package] = importlib.metadata.version(package)
    except Exception:
        pass
try:
    import clash_royale
    import gymnasium as gym
    env = gym.make('clash-royale', render_mode='rgb_array')
    result['action_space_actual'] = str(env.action_space)
    result['observation_space_actual'] = str(env.observation_space)
    raw = env.unwrapped
    result['unwrapped_reset'] = repr(raw.reset(seed=42))
    result['unwrapped_step'] = repr(raw.step(0))
    result['unwrapped_render'] = repr(raw.render())
    try:
        obs, info = env.reset(seed=42)
        result['wrapped_reset_success'] = True
    except Exception as e:
        result['wrapped_reset_success'] = False
        result['wrapped_reset_error'] = repr(e)
    env.close()
except Exception as e:
    result['exception'] = repr(e)
    result['traceback'] = traceback.format_exc()
    # Bypass only Gymnasium's validation to inspect the upstream placeholders.
    try:
        env = gym.make('clash-royale', render_mode='rgb_array', disable_env_checker=True)
        result['action_space_actual'] = str(env.action_space)
        result['observation_space_actual'] = str(env.observation_space)
        result['unwrapped_reset'] = repr(env.unwrapped.reset(seed=42))
        result['unwrapped_step'] = repr(env.unwrapped.step(0))
        result['unwrapped_render'] = repr(env.unwrapped.render())
        env.close()
    except Exception as probe_error:
        result['unchecked_exception'] = repr(probe_error)
out = ROOT / 'outputs' / 'audit_candidates' / 'gym_smoke.json'
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
