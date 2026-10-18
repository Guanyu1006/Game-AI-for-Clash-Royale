"""Save public versioned card metadata and an independent candidate catalog."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/audit'
OUT.mkdir(parents=True, exist_ok=True)
COMMIT = 'd5461b0a59bff33c4da2fc845b07275b66b2d6ff'
SOURCE = ROOT / 'third_party/audit_candidates/crforge_git_sparse/data/src/main/resources/cards'

def remote(url, dest):
    result = {'url':url, 'access_date':'2026-10-13', 'path':str(dest.relative_to(ROOT))}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        data = opener.open(urllib.request.Request(url, headers={'User-Agent':'RoyaleResearchAudit/1.0'}), timeout=20).read()
        dest.write_bytes(data)
        result.update(status='downloaded', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    except Exception as e:
        result.update(status='failed', error=repr(e))
    return result

if __name__ == '__main__':
    sources = []
    for file in ['cards.json', 'cards_evo.json', 'cards_i18n.json', 'cards_stats.json', 'cards_stats_spell.json']:
        row = remote(f'https://raw.githubusercontent.com/RoyaleAPI/cr-api-data/{COMMIT}/docs/json/{file}', OUT / ('royaleapi_'+file))
        row['commit'] = COMMIT
        row['source_type'] = 'dataset author primary repository; community metadata, not official API'
        row['license'] = 'No repository license found in prior data package; inspect README/source before redistribution'
        sources.append(row)
    sources.append(remote(f'https://raw.githubusercontent.com/RoyaleAPI/cr-api-data/{COMMIT}/README.md', OUT / 'royaleapi_README.md'))
    sources.append(remote('https://supercell.com/en/games/clashroyale/blog/release-notes/october-balance-changes-2026/', OUT / 'official_october_2026_balance.html'))
    sources.append(remote('https://support.clashroyale.com/hc/en-us/articles/56455725169563-Watt-s-New-October-2026', OUT / 'official_october_2026_new_content.html'))
    cards = json.loads((SOURCE / 'cards.json').read_text(encoding='utf-8'))
    units = json.loads((SOURCE / 'units.json').read_text(encoding='utf-8'))
    projectiles = json.loads((SOURCE / 'projectiles.json').read_text(encoding='utf-8'))
    (OUT / 'crforge_cards_snapshot.json').write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['card_id','name','type','rarity','elixir','unit_ref','projectile_ref','variant_count','is_evolution',
              'coverage_status','implementation_path','source_commit','official_match_verified','notes']
    with (OUT / 'crforge_card_catalog.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for card in cards:
            refs = []
            if card.get('unit'): refs.append('unit:'+card['unit'])
            if card.get('projectile'): refs.append('projectile:'+card['projectile'])
            row = {'card_id':card['id'], 'name':card['name'], 'type':card['type'], 'rarity':card.get('rarity'),
                   'elixir':card.get('cost'), 'unit_ref':card.get('unit'), 'projectile_ref':card.get('projectile'),
                   'variant_count':len(card.get('variants',[])), 'is_evolution':'ev1' in card['id'],
                   'coverage_status':'unverified', 'implementation_path':str(SOURCE.relative_to(ROOT) / 'cards.json'),
                   'source_commit':'b41cf9b6276945152ec4c869ce56f98a5fa3f513', 'official_match_verified':False,
                   'notes':'catalog row only; does not establish behavior. Includes event/super/hero/evolution variants; not 240 standard cards'}
            writer.writerow(row)
    manifest = {'research_target_reference_date':'2026-10-06 official balance announcement',
                'simulation_version':'Community source snapshot; no certified mapping to official client version/balance',
                'catalog_denominators':{'crforge_config_rows':len(cards), 'crforge_unit_records':len(units),
                    'crforge_projectile_records':len(projectiles)},
                'sources':sources}
    if (OUT / 'royaleapi_cards.json').exists():
        r = json.loads((OUT / 'royaleapi_cards.json').read_text(encoding='utf-8'))
        manifest['catalog_denominators']['royaleapi_card_rows'] = len(r)
        with (OUT / 'public_card_catalog.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['key','name','sc_key','id','elixir','type','rarity','arena','is_evolved','source_commit','status'])
            writer.writeheader()
            for card in r:
                row = {key:card.get(key) for key in writer.fieldnames}
                row.update(source_commit=COMMIT, status='catalog_only_unverified_behavior')
                writer.writerow(row)
    (OUT / 'catalog_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
