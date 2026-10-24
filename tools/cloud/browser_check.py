"""Optional real-browser checks for the local Codespaces workbench."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import traceback

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tools.cloud.workspace import local,make_server,output,sha,write


def check(destination=None,*,replay_metadata=None):
    folder=local(destination) if destination else output('browser-check')
    folder.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(local('.cache/cloud/playwright')))
    library_root=local('.cache/cloud/browser-libs/root')
    if library_root.is_dir():
        candidates=[str(library_root/'usr/lib/x86_64-linux-gnu'),str(library_root/'lib/x86_64-linux-gnu')]
        os.environ['LD_LIBRARY_PATH']=os.pathsep.join(candidates+([os.environ['LD_LIBRARY_PATH']] if os.environ.get('LD_LIBRARY_PATH') else []))
    fonts=local('.cache/cloud/browser-libs/fonts.conf')
    if fonts.is_file():os.environ.setdefault('FONTCONFIG_FILE',str(fonts))
    result={'schema':'cloud-browser-check.v1','status':'running','checks':[],'page_errors':[],
        'native_rpc_calls':0,'training_jobs':0,'requested_replay_metadata':str(replay_metadata) if replay_metadata is not None else None,
        'replay_visualization':'saved_public_observation_schematic_not_native_pixels',
        'scope':'Real Chromium against local HTTP; saved public observation tower schematic and approximate sandbox only; no native game pixels'}
    server=None;thread=None
    def start():
        nonlocal server,thread
        server=make_server(port=0,simulation_root=folder/'sandbox',replay_metadata=replay_metadata)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        return f'http://127.0.0.1:{server.server_port}'
    def stop():
        nonlocal server,thread
        if server:
            server.shutdown();server.server_close();thread.join(timeout=3);server=None
    def passed(name):
        result['checks'].append(name);write(folder/'report.json',result)
    try:
        if replay_metadata is not None:
            selected_metadata=local(replay_metadata)
            replay_metadata=selected_metadata.relative_to(ROOT).as_posix()
            result['replay_source']={'metadata':replay_metadata,'metadata_sha256':sha(selected_metadata)}
        from playwright.sync_api import sync_playwright,expect
        base=start()
        with sync_playwright() as engine:
            browser=engine.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
            page.on('pageerror',lambda error:result['page_errors'].append(str(error)))
            page.goto(base,wait_until='networkidle')
            expect(page.locator('#result')).to_contain_text('offline_research')
            page.get_by_role('button',name='运行已保存 NumPy 模型',exact=True).click()
            expect(page.locator('#result')).to_contain_text('matches_saved_decoding')
            page.screenshot(path=str(folder/'workbench.png'),full_page=True);passed('workbench_saved_model_inference')
            page.get_by_role('link',name='打开保存观测回放与可交互模拟器').click()
            catalog=page.request.get(base+'/api/replay/catalog').json()
            assert catalog['episodes'], 'No saved replay episode options'
            provenance=catalog['provenance'];metadata_path=local(provenance['metadata'])
            metadata_sha256=sha(metadata_path)
            assert metadata_sha256==provenance['metadata_sha256'], 'Replay metadata changed while loading'
            if replay_metadata is not None:
                assert metadata_path==local(replay_metadata), 'Selected replay metadata was not used'
            result['replay_source']={'metadata':metadata_path.relative_to(ROOT).as_posix(),
                'metadata_sha256':metadata_sha256,'rows':provenance['rows'],
                'rows_sha256':provenance['rows_sha256'],'source_scope':provenance['source_scope'],
                'version_fingerprint_sha256':provenance['version_fingerprint_sha256']}
            default_episode=catalog['episodes'][0]
            initial=next((item for item in catalog['episodes'] if item['owner']==0 and item['frames']>=2),None)
            owner_one=next((item for item in catalog['episodes'] if item['owner']==1 and item['frames']>=2),None)
            assert initial is not None, 'Owner 0 with at least two saved frames is required for the browser check'
            assert owner_one is not None, 'Owner 1 with at least two saved frames is required for the browser check'
            result['replay_selection']={'default_episode':default_episode,'initial':initial,'owner_one':owner_one}
            expect(page.locator('#replayInfo')).to_contain_text(default_episode['episode_id'])
            if initial!=default_episode:
                page.locator('#episode').select_option(f"{initial['episode_id']}|0")
                page.locator('#replayReset').click()
            expect(page.locator('#replayInfo')).to_contain_text(initial['episode_id'])
            expect(page.locator('#replayInfo')).to_contain_text(f"owner {initial['owner']}")
            expect(page.locator('#frame')).to_have_value('0')
            initial_state=page.request.get(base+'/api/replay/state').json()
            assert initial_state['episode_id']==initial['episode_id'] and initial_state['owner']==initial['owner']
            expect(page.locator('#history tr')).to_have_count(len(initial_state['observation']['history']))
            expect(page.locator('#simInfo')).to_contain_text('对局进行中')
            # Populate one episode's later history before switching owners, so
            # reset must actually discard the previous frame's rendered rows.
            page.locator('#frame').evaluate("(element,index)=>{element.value=String(index);element.dispatchEvent(new Event('change',{bubbles:true}))}",initial['frames']-1)
            expect(page.locator('#replayInfo')).to_contain_text(f"帧 {initial['frames']}/{initial['frames']}")
            initial_last=page.request.get(base+'/api/replay/state').json()
            assert initial_last['index']==initial['frames']-1
            initial_last_history=initial_last['observation']['history']
            expect(page.locator('#history tr')).to_have_count(len(initial_last_history))
            result['replay_selection']['initial_last_history_rows']=len(initial_last_history)
            page.locator('#episode').select_option(f"{owner_one['episode_id']}|{owner_one['owner']}")
            page.locator('#replayReset').click()
            expect(page.locator('#replayInfo')).to_contain_text(owner_one['episode_id'])
            expect(page.locator('#replayInfo')).to_contain_text('owner 1')
            expect(page.locator('#frame')).to_have_value('0')
            owner_first=page.request.get(base+'/api/replay/state').json()
            assert owner_first['episode_id']==owner_one['episode_id'] and owner_first['owner']==1 and owner_first['index']==0
            first_history=owner_first['observation']['history']
            expect(page.locator('#history tr')).to_have_count(len(first_history))
            page.locator('#next').click();expect(page.locator('#frame')).to_have_value('1')
            page.locator('#prev').click();expect(page.locator('#frame')).to_have_value('0')
            expect(page.locator('#history tr')).to_have_count(len(first_history))
            restored=page.request.get(base+'/api/replay/state').json()
            assert restored['observation']['history']==first_history, 'Backward seek retained later history'
            assert restored['observation']==owner_first['observation'], 'Backward seek changed the saved first-frame observation'
            result['replay_selection']['owner_first_history_rows']=len(first_history)
            passed('replay_owner_switch_and_backward_history_isolation')
            page.locator('#seed').fill('3');page.locator('#maxTime').fill('10');page.locator('#simReset').click()
            expect(page.locator('#simInfo')).to_contain_text('/ 10 秒')
            state=page.request.get(base+'/api/simulator/state').json()
            slot=next(h['slot'] for h in state['hand'] if h['kind']=='troop' and h['cost']<=5)
            page.locator('#cards button').nth(slot).click()
            canvas=page.locator('#simBoard');box=canvas.bounding_box()
            canvas.click(position={'x':box['width']*4/18,'y':box['height']*25/32})
            expect(page.locator('#feedback')).to_contain_text('消费 true')
            after=page.request.get(base+'/api/simulator/state').json()
            assert after['observation']['units'] and after['observation']['time']==1
            page.screenshot(path=str(folder/'replay-simulator.png'),full_page=True);passed('canvas_card_play_with_actual_units_and_feedback')
            page.locator('#save').click();expect(page.locator('#saved')).to_be_visible()
            saved=json.loads(page.locator('#saved').inner_text());run_id=saved['run_id']
            page.locator('#seed').fill('99');page.locator('#maxTime').fill('2');page.locator('#simReset').click()
            page.locator('#seconds').fill('2');page.locator('#simAuto').click()
            expect(page.locator('#simInfo')).to_contain_text('已结束')
            page.get_by_text('回看当前 / 已保存快照（不会回滚活动对局）',exact=True).click()
            page.locator('#runId').fill(run_id);page.locator('#openSaved').click()
            expect(page.locator('#snapshotInfo')).to_contain_text('活动对局未回滚')
            assert page.request.get(base+'/api/simulator/state').json()['seed']==99
            passed('terminal_reset_and_saved_replay_keep_active_game')
            stop();base=start()
            page.goto(base+'/research',wait_until='networkidle')
            expect(page.locator('#replayInfo')).to_contain_text(default_episode['episode_id'])
            expect(page.locator('#frame')).to_have_value('0')
            restarted_catalog=page.request.get(base+'/api/replay/catalog').json()
            assert restarted_catalog['provenance']['metadata']==provenance['metadata']
            assert restarted_catalog['provenance']['metadata_sha256']==metadata_sha256, 'Server restart changed the replay source'
            expect(page.locator('#simInfo')).to_contain_text('对局进行中')
            page.get_by_text('回看当前 / 已保存快照（不会回滚活动对局）',exact=True).click()
            page.locator('#runId').fill(run_id);page.locator('#openSaved').click()
            expect(page.locator('#snapshotInfo')).to_contain_text(run_id)
            expect(page.locator('#simInfo')).to_contain_text('回看快照')
            assert json.loads(page.locator('#simState').text_content())['seed']==3
            assert page.request.get(base+'/api/simulator/state').json()['seed']==0
            expect(page.locator('#error')).to_be_empty();passed('saved_snapshot_survives_server_restart')
            result['browser_version']=browser.version;browser.close()
        if result['page_errors']:raise AssertionError('Browser page errors: '+str(result['page_errors']))
        result['status']='passed';result['saved_run_id']=run_id
    except Exception as exc:
        result.update(status='failed',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    finally:
        stop();result['report_path']=str((folder/'report.json').relative_to(ROOT));write(folder/'report.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output')
    parser.add_argument('--replay-metadata',help='Repository-local fair saved-export metadata; default uses the reviewed portable sample')
    args=parser.parse_args()
    result=check(args.output,replay_metadata=args.replay_metadata);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
