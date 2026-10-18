"""One documented metadata migration after narrowing the numeric schema.

No games are rewritten. A verified 100-game state comparison permits retaining
the existing default-physics evaluation; custom balance experiments stay separate.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_lab.backend import catalog_payload
from royale_lab.runtime import target

OLD = 'b98e3b367896375ec4318fa815f3743fd4d890f45082c046dd973af3001c6003'
NEW = '9095f8eb1cd2fafa6115afb342b4888c6ebe23436e97e908919a1b8cee21129e'
MODULE = '6160b968f9dfde6ab9b047627596c5d50383b0af8728ed5036fc49239edccd53'

def main():
    evidence_path = ROOT/'outputs/native_engine_numeric_replay_equivalence.json'
    evidence = json.loads(evidence_path.read_text(encoding='utf-8-sig'))
    native = ROOT/'third_party/ClashRoyaleAi/python_ai/clash_royale_env.cp314-win_amd64.pyd'
    assert hashlib.sha256(native.read_bytes()).hexdigest() == MODULE
    assert evidence['passed'] and evidence['games'] == 100 and not evidence['differences']
    assert evidence['full_state_comparisons_per_variant'] == 169994
    assert evidence['module_hashes'][1:] == [MODULE, MODULE]
    assert catalog_payload()['balance']['hash'] == NEW
    path = ROOT/'outputs/evaluation/main/experiment_config.json'
    metadata = json.loads(path.read_text(encoding='utf-8-sig'))
    before = metadata['configuration']['balance_hash']
    assert before in (OLD, NEW), before
    archive = target('outputs/archive/numeric-schema-v1/experiment_config.before-final.json')
    if before == OLD:
        assert not archive.exists() or archive.read_bytes() == path.read_bytes()
        archive.write_bytes(path.read_bytes())
        metadata['configuration']['balance_hash'] = NEW
        path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    record = {'previous_hash': OLD, 'current_hash': NEW, 'module_sha256': MODULE,
              'reason': 'Remove accepted-but-unused fields from schema; default battle physics unchanged in verified samples.',
              'games_compared': 100, 'full_state_points_per_variant': 169994,
              'evidence': str(evidence_path.relative_to(ROOT)),
              'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
              'old_metadata': str(archive.relative_to(ROOT)),
              'command': '.venv/Scripts/python.exe tools/migrate_balance_metadata.py',
              'limitations': 'Sampled local-engine equivalence, not a proof for all states or official gameplay.'}
    target('outputs/platform/numeric_schema_migration.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))

if __name__ == '__main__':
    main()
