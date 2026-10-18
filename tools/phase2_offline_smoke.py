"""Cold-start the project VM in restricted usernet and prove real offline play.

Default invocation only prints a plan. --run requires the exclusive native
window, a real checkpoint, and all dependencies already downloaded.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import runpy
import socket
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.viewer import AndroidBridge, DEFAULT_EPISODE, write_json

FIXED_URL='https://raw.githubusercontent.com/remote-android/redroid-doc/eba9a489990f3f558b7115f25fdc782d610ee8ad/README.md'
POSITIVE_CONTROL='outputs/phase2/secondary/network_preparation_positive_control.stdout.log'


def sha(path):
    with local(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class Runner:
    def __init__(self, folder):self.folder=folder;self.commands=[]
    def run(self,label,argv,timeout=120,required=True):
        stdout=self.folder/(label+'.stdout.log');stderr=self.folder/(label+'.stderr.log')
        record={'label':label,'argv':[str(a) for a in argv],'cwd':str(ROOT),'started_utc':datetime.now(timezone.utc).isoformat()}
        start=time.monotonic()
        with stdout.open('wb') as out,stderr.open('wb') as err:
            try:
                proc=subprocess.run([str(a) for a in argv],cwd=ROOT,stdout=out,stderr=err,timeout=timeout)
                record['exit_code']=proc.returncode
            except Exception as exc:record['error']=f'{type(exc).__name__}: {exc}'
        record.update(wall_seconds=time.monotonic()-start,stdout=str(stdout.relative_to(ROOT)),stderr=str(stderr.relative_to(ROOT)),
                      stdout_sha256=sha(stdout),stderr_sha256=sha(stderr))
        self.commands.append(record);write_json(self.folder/'commands.json',self.commands)
        if required and record.get('exit_code')!=0:raise RuntimeError(f'{label} failed; see saved raw logs: {record}')
        return record


def read_json_log(folder, record):
    return json.loads(local(record['stdout']).read_text(encoding='utf-8-sig'))


def actual_process(runner,label,pid):
    if type(pid) is not int or pid<=0:raise ValueError('Invalid owned process PID')
    command=f"[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json"
    record=runner.run(label,['powershell.exe','-NoProfile','-Command',command],30)
    text=local(record['stdout']).read_text(encoding='utf-8-sig').strip()
    value=json.loads(text) if text else None
    return value


def stop_owned_tunnel(runner):
    path=local('outputs/phase2/secondary/tunnel_26789.json')
    if not path.exists():return {'present':False}
    metadata=json.loads(path.read_text());process=actual_process(runner,'owned_tunnel_before_coldboot',metadata['pid'])
    if process is None:return {'present':False,'old_pid':metadata['pid']}
    line=process.get('CommandLine') or ''
    if process.get('Name','').casefold()!='ssh.exe' or '127.0.0.1:26789:127.0.0.1:26789' not in line or 'guest-key' not in line:
        raise RuntimeError('Refusing to stop a process that does not match the owned project SSH forward')
    os.kill(process['ProcessId'],signal.SIGTERM)
    return {'stopped_owned_pid':process['ProcessId'],'verified_commandline':line}


NETWORK_SCRIPT=r'''import json,socket,time,urllib.request
url=URL
rows=[]
for route,opener in [('direct',urllib.request.build_opener(urllib.request.ProxyHandler({}))),('existing_host_proxy',urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://10.0.2.2:17890'})))]:
 t=time.monotonic()
 try:
  with opener.open(url,timeout=6) as response:result={'status':response.status,'prefix':response.read(64).decode(errors='replace')}
 except Exception as exc:result={'error':type(exc).__name__+': '+str(exc)}
 rows.append({'route':route,'url':url,'result':result,'wall_seconds':time.monotonic()-t})
for address in ['1.1.1.1','8.8.8.8']:
 try:
  connection=socket.create_connection((address,443),timeout=3);connection.close();value='connected'
 except Exception as exc:value=type(exc).__name__+': '+str(exc)
 rows.append({'route':'direct_ip_no_dns','address':address,'port':443,'result':value})
print(json.dumps({'checks':rows},indent=2))
'''


def validate_native_trace(folder, rendered=False):
    rows=[];events={};states=[];commands=[]
    for path in sorted(folder.rglob('wire.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            row=json.loads(line);rows.append(row);commands.append(row.get('command',''))
            response=row.get('response')
            if isinstance(response,dict) and response.get('schema')=='cr-minimal.v4' and response.get('ok') is True:
                states.append(response)
                for event in response.get('plays',[]):events[(response['generation'],event[0])]=event
    if not states or not events:raise AssertionError('No actual native minimal states and consumed events in raw RPC')
    first,last=states[0],states[-1]
    if first['finalized'] or not last['finalized'] or last['tick']<=first['tick']:
        raise AssertionError('Raw native trace did not move from nonterminal initialization to natural terminal')
    configure_prefix='configure-native ' if rendered else 'configure '
    advance_prefixes=('advance-native ',) if rendered else ('minimal-transition ','step ')
    if not any(c.startswith(configure_prefix) for c in commands) or not any(c.startswith(advance_prefixes) for c in commands):
        raise AssertionError('Missing actual native configuration/advance calls')
    return {'raw_rpc_rows':len(rows),'native_state_responses':len(states),'actual_consumed_events':len(events),
            'consumed_events':[events[key] for key in sorted(events)],
            'initial_tick':first['tick'],'terminal_tick':last['tick'],'terminal_result':last['result'],
            'terminal_crowns':last['crowns'],'native_generations':sorted({s['generation'] for s in states})}


def validate_replay(folder):
    episode=json.loads((folder/'episode.json').read_text());files=episode['files']
    for artifact in files.values():
        if sha(artifact['path'])!=artifact['sha256']:raise AssertionError('Replay artifact hash changed')
    source=local(episode['source_episode']);source_raw=json.loads(source.read_text())
    if sha(source)!=episode['source_episode_sha256']:raise AssertionError('Immutable replay source hash changed')
    rows=[json.loads(line)['raw_capture'] for line in local(files['states.jsonl']['path']).read_text().splitlines() if line]
    actions=json.loads(local(files['actions.json']['path']).read_text())['actions']
    if len(actions)!=len(episode['source_actions']) or any(r.get('execution_status')!='executed' for r in actions):
        raise AssertionError('Real source action execution incomplete')
    if not rows[-1]['finalized'] or rows[-1]['tick']<=rows[0]['tick']:raise AssertionError('Replay lacks natural terminal')
    actual=validate_native_trace(folder)
    if actual['actual_consumed_events']!=len(actions):raise AssertionError('Source journal/native consumption cardinality mismatch')
    return {'source_episode_id':episode['source_episode_id'],'source_episode_path':str(source.relative_to(ROOT)),
            'source_sha256':sha(source),'source_journal_actions':len(actions),'raw_trace':actual,
            'raw_terminal_state_sha256':hashlib.sha256(json.dumps(rows[-1],sort_keys=True).encode()).hexdigest(),
            'quality_report_preserved':episode['quality'],'source_schema':source_raw.get('schema_version')}


def validate_agent(folder,checkpoint_sha):
    report=json.loads((folder/'report.json').read_text())
    if report['checkpoint']['file_sha256']!=checkpoint_sha:raise AssertionError('Agent process loaded another checkpoint')
    if len(report['games'])!=1:raise AssertionError('Offline smoke requires one explicit complete model game')
    game=report['games'][0]
    if not game.get('natural_terminal') or game.get('truncated') or game.get('error'):
        raise AssertionError('Agent game did not reach a natural terminal')
    game_folder=folder/'game-000'
    decisions=[json.loads(line) for line in (game_folder/'decisions.jsonl').read_text().splitlines() if line]
    model_predictions=[r for r in decisions if r['model_prediction']['kind']=='play_card']
    feedback=json.loads((game_folder/'execution_feedback.json').read_text())['actions']
    model_events=[r for r in feedback if ':model:' in r['request_id'] and r.get('card_consumed') and r.get('card_event')]
    if not model_predictions or not model_events:raise AssertionError('Saved model never actually played a consumed card; no forced substitute allowed')
    actual=validate_native_trace(game_folder)
    observed={tuple(event) for event in actual['consumed_events']}
    if len(model_events)!=len(model_predictions) or any(tuple(row['card_event']) not in observed for row in model_events):
        raise AssertionError('Model receipts do not match every actual model play and raw native event')
    raw_states=[json.loads(line)['state'] for line in (game_folder/'states.jsonl').read_text().splitlines() if line]
    if not raw_states[-1]['finalized'] or raw_states[-1]['tick']!=actual['terminal_tick']:
        raise AssertionError('Agent raw state log does not corroborate natural terminal')
    return {'checkpoint_sha256':checkpoint_sha,'actual_model_play_predictions':len(model_predictions),
            'actual_model_consumed_events':len(model_events),'raw_trace':actual,
            'model_event_evidence':[{'request_id':r['request_id'],'card_event':r['card_event'],'matched_by':r['matched_by']} for r in model_events],
            'counts_toward_G':False,'scope':'One cold-start offline game; the separate10-game G denominator is not changed'}


def guarded_exec(config_path):
    """Process-local Python network denial, in addition to guest usernet isolation.

    This is not a kernel sandbox. It covers the audited Python controller's
    socket APIs; native engine egress is separately blocked in the guest.
    """
    config=json.loads(local(config_path).read_text());script=local(config['argv'][0]);events=[]
    original_connect=socket.socket.connect;original_ex=socket.socket.connect_ex
    original_resolve=socket.getaddrinfo;original_sendto=socket.socket.sendto
    def allow(host,operation):
        value=host.decode() if isinstance(host,bytes) else host
        if value is None or value=='localhost':return
        try:permitted=ipaddress.ip_address(value).is_loopback
        except ValueError:permitted=False
        if not permitted:
            events.append({'operation':operation,'host':str(value),'outcome':'denied_before_DNS_or_socket_connect',
                           'at_utc':datetime.now(timezone.utc).isoformat()})
            raise PermissionError('Offline controller permits only loopback Python networking')
    def connect(instance,address):
        if isinstance(address,tuple):allow(address[0],'socket.connect')
        return original_connect(instance,address)
    def connect_ex(instance,address):
        if isinstance(address,tuple):allow(address[0],'socket.connect_ex')
        return original_ex(instance,address)
    def resolve(host,*args,**kwargs):allow(host,'socket.getaddrinfo');return original_resolve(host,*args,**kwargs)
    def sendto(instance,*args,**kwargs):
        if args and isinstance(args[-1],tuple):allow(args[-1][0],'socket.sendto')
        return original_sendto(instance,*args,**kwargs)
    socket.socket.connect=connect;socket.socket.connect_ex=connect_ex;socket.getaddrinfo=resolve;socket.socket.sendto=sendto
    record={'schema':'phase2-offline-python-loopback-guard.v1','script':str(script.relative_to(ROOT)),
            'script_sha256':sha(script),'argv':config['argv'],'exit_code':None,
            'scope':'Python socket API enforcement only, not a kernel sandbox; actual native guest separately has restrict=on'}
    try:
        try:
            connection=socket.create_connection(('1.1.1.1',443),timeout=0.1);connection.close()
        except PermissionError as exc:record['external_canary']={'denied':True,'exception':str(exc)}
        else:raise AssertionError('Process-local external network canary was not denied')
        sys.argv=[str(script),*config['argv'][1:]];runpy.run_path(str(script),run_name='__main__');record['exit_code']=0
    except SystemExit as exc:
        record['exit_code']=exc.code if isinstance(exc.code,int) else (0 if exc.code is None else 1)
        raise
    except Exception as exc:
        record['exception']=f'{type(exc).__name__}: {exc}';raise
    finally:
        record['denied_attempts']=events;write_json(config['guard_report'],record)


def run_guarded(runner,label,argv):
    command_file=runner.folder/(label+'.guard-command.json');guard_report=runner.folder/(label+'.network_guard.json')
    write_json(command_file,{'argv':argv[1:],'guard_report':str(guard_report.relative_to(ROOT))})
    runner.run(label,[argv[0],'tools/phase2_offline_smoke.py','--guard-exec-json',str(command_file.relative_to(ROOT))],600)
    guard=json.loads(guard_report.read_text())
    if guard.get('exit_code')!=0 or guard.get('external_canary',{}).get('denied') is not True:
        raise AssertionError('Actual child Python network guard/target execution did not pass')
    return {'path':str(guard_report.relative_to(ROOT)),'sha256':sha(guard_report),'actual_external_canary':guard['external_canary']}


def plan(args):
    folder=local(args.output)
    return {'mode':'plan_only; no VM/ADB/RPC operations','output':str(folder.relative_to(ROOT)),
            'cold_start':'fresh application' if args.app_only else 'guest full poweroff + restricted QEMU cold boot + fresh Android/application',
            'positive_control_source':POSITIVE_CONTROL,'same_url':FIXED_URL,
            'replay_argv':[str(local('.venv-phase2-core/Scripts/python.exe')),'tools/phase2_replay.py',args.source_id,'--output',str((folder/'replay').relative_to(ROOT))],
            'agent_argv':[str(local('.venv-phase2-data/Scripts/python.exe')),'tools/phase2_evaluate_native.py','--checkpoint',args.checkpoint,
                          '--episodes','1','--offline-smoke','--output',str((folder/'agent').relative_to(ROOT))],
            'estimated_wall_time_minutes':'4–8 depending guest boot and actual model match; no hardcoded success'}


def execute(args):
    if not args.exclusive_runtime:raise ValueError('An exclusive native runtime window is required')
    folder=local(args.output);folder.mkdir(parents=True,exist_ok=False);runner=Runner(folder);bridge=AndroidBridge()
    report={'schema':'phase2-offline-coldstart-smoke.v1','status':'fail','planned_commands':plan(args),
            'global_firewall_modified':False,'official_account_used':False,'fallback_simulator_used':False}
    try:
        checkpoint=local(args.checkpoint);checkpoint_sha=sha(checkpoint);metadata=json.loads(checkpoint.read_text())
        weights=local(metadata['weights']['path']);weights_sha=sha(weights)
        if weights_sha!=metadata['weights']['sha256']:raise ValueError('Checkpoint weights hash mismatch before cold start')
        report['checkpoint']={'path':str(checkpoint.relative_to(ROOT)),'sha256':checkpoint_sha,'weights':str(weights.relative_to(ROOT)),'weights_sha256':weights_sha}
        baseline=local(POSITIVE_CONTROL).read_bytes();positive=json.JSONDecoder().raw_decode(baseline.decode())[0]
        positive_row=next(row for row in positive['checks'] if row['route']=='existing_host_proxy')
        if positive_row['url']!=FIXED_URL or positive_row['result'].get('status')!=200:raise AssertionError('Historical same-URL reachable positive control missing')
        report['preparation_positive_control']={'path':POSITIVE_CONTROL,'sha256':hashlib.sha256(baseline).hexdigest(),'record':positive_row}
        before=runner.run('guest_boot_before',bridge.ssh+['cat /proc/sys/kernel/random/boot_id'],30)
        old_boot=local(before['stdout']).read_text().strip();report['old_boot_id']=old_boot
        if not args.app_only:
            report['old_tunnel']=stop_owned_tunnel(runner)
            runner.run('guest_poweroff',bridge.ssh+['sync; sudo shutdown -h now'],60)
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                try:
                    with socket.create_connection(('127.0.0.1',22226),timeout=1):pass
                except OSError:break
                time.sleep(1)
            else:raise TimeoutError('Guest did not fully power off; refusing a second VM on the same disk')
            runner.run('qemu_cold_start',[sys.executable,'tools/phase2_secondary_vm.py','qemu-start','--accel','whpx','--isolated'],30)
        qemu=json.loads(local('outputs/phase2/secondary/qemu_vm_process.json').read_text())
        actual=actual_process(runner,'qemu_actual_process',qemu['pid'])
        if not actual or 'restrict=on' not in actual.get('CommandLine','') or 'restrict=off' in actual.get('CommandLine',''):
            raise AssertionError('Live QEMU process does not actually use restricted usernet')
        report['actual_qemu_process']=actual
        for attempt in range(1,40):
            record=runner.run(f'guest_boot_after_{attempt:02}',bridge.ssh+['cat /proc/sys/kernel/random/boot_id'],15,False)
            if record.get('exit_code')==0:break
            time.sleep(2)
        else:raise TimeoutError('Restricted cold guest SSH did not become ready')
        report['new_boot_id']=local(record['stdout']).read_text().strip()
        if not args.app_only and report['new_boot_id']==old_boot:raise AssertionError('VM boot_id did not change')
        runner.run('android_container_start',bridge.ssh+['sudo phase2-android-start'],60)
        for attempt in range(1,50):
            record=runner.run(f'android_boot_{attempt:02}',bridge.ssh+['sudo docker exec phase2-android getprop sys.boot_completed'],15,False)
            if record.get('exit_code')==0 and local(record['stdout']).read_text().strip()=='1':break
            time.sleep(2)
        else:raise TimeoutError('Actual Android framework cold boot did not complete')
        network_command="python3 - <<'PY'\n"+NETWORK_SCRIPT.replace('url=URL','url='+repr(FIXED_URL))+"\nPY"
        negative=runner.run('isolated_network_actual',bridge.ssh+[network_command],60)
        network=read_json_log(folder,negative);report['isolated_network']=network
        if any('status' in row.get('result',{}) for row in network['checks'] if isinstance(row.get('result'),dict)):
            raise AssertionError('An external URL unexpectedly remained reachable')
        if any(row.get('result')=='connected' for row in network['checks']):raise AssertionError('A direct external TCP connection succeeded')
        runner.run('adb_cold_application_start',bridge.ssh+['adb connect 127.0.0.1:5555 && adb -s 127.0.0.1:5555 shell am force-stop nullsroyale.rel.free && '
                   'adb -s 127.0.0.1:5555 shell am start -n nullsroyale.rel.free/com.supercell.clashroyale.GameApp && '
                   'adb -s 127.0.0.1:5555 forward tcp:26789 tcp:26789'],60)
        if not args.app_only:runner.run('local_probe_tunnel',[sys.executable,'tools/phase2_secondary_guest_tunnel.py','--port','26789'],30)
        from royale_phase2.probe import ProbeClient
        for attempt in range(1,40):
            client=ProbeClient(timeout=5)
            try:
                status=client.status()
                if status.get('contentReady') and status.get('mode')=='headless':break
            except Exception as exc:report['last_probe_wait_error']=str(exc)
            finally:client.close()
            time.sleep(1)
        else:raise TimeoutError('Local actual native probe did not initialize offline')
        report['local_native_status_after_cold_start']=status
        runner.run('loaded_native_component_recheck',[sys.executable,'tools/phase2_secondary_guest_exec.py',
                   'tools/phase2_secondary_guest_native_identity.sh','--label','offline_B_loaded_native','--timeout','60'],90)
        native_raw=local('outputs/phase2/secondary/offline_B_loaded_native.stdout.log').read_text()
        (folder/'loaded_native_identity.stdout.log').write_text(native_raw,encoding='utf-8')
        manifest=json.loads(local('data/phase2/version_manifest.json').read_text())
        verified={}
        for filename,role in [('libg.so','native_library'),('libcrprobe.so','native_probe')]:
            expected={asset['sha256'] for asset in manifest['assets'] if asset.get('role')==role}
            hashes=[line.split()[0] for line in native_raw.splitlines() if line.endswith('/'+filename) and len(line.split()[0])==64]
            mapped=any(filename in line and ('r--p' in line or 'r-xp' in line) for line in native_raw.splitlines())
            if not hashes or not set(hashes).issubset(expected) or not mapped:raise AssertionError('Fresh process native hash/maps mismatch: '+filename)
            verified[filename]={'installed_sha256':hashes,'actual_process_mapping':mapped}
        report['fresh_loaded_native_components']=verified
        runner.run('installed_content_recheck',[sys.executable,'tools/phase2_secondary_guest_exec.py',
                   'tools/phase2_secondary_guest_installed_hashes.sh','--label','offline_B_installed_hashes','--timeout','60'],90)
        installed=json.loads(local('outputs/phase2/secondary/offline_B_installed_hashes.stdout.log').read_text())
        if not installed['all_verified'] or installed['file_count']!=652:raise AssertionError('Installed resource recheck failed')
        write_json(folder/'installed_content.json',installed)
        commands=plan(args)
        report['replay_controller_guard']=run_guarded(runner,'real_replay_new_process',commands['replay_argv'])
        report['replay']=validate_replay(folder/'replay')
        # --offline-smoke permits one game but never counts it as G10 acceptance.
        report['agent_controller_guard']=run_guarded(runner,'checkpoint_agent_new_process',commands['agent_argv'])
        report['agent']=validate_agent(folder/'agent',checkpoint_sha)
        if sha(checkpoint)!=checkpoint_sha or sha(weights)!=weights_sha:raise AssertionError('Checkpoint changed during offline evaluation')
        after=runner.run('isolated_network_after_games',bridge.ssh+[network_command],60)
        network_after=read_json_log(folder,after)
        if any(row.get('result')=='connected' or (isinstance(row.get('result'),dict) and 'status' in row['result']) for row in network_after['checks']):
            raise AssertionError('External network became reachable during the actual games')
        report.update(status='pass',cold_start_scope='application' if args.app_only else 'full Linux/Android guest and application',
                      source_control_ssh=True,network_after_games=network_after)
    except Exception as exc:report.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    finally:
        report['commands']=runner.commands
        artifacts=[]
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.name!='report.json':artifacts.append({'path':str(path.relative_to(ROOT)),'bytes':path.stat().st_size,'sha256':sha(path)})
        report['artifacts']=artifacts;write_json(folder/'report.json',report)
    print(json.dumps({'report':str((folder/'report.json').relative_to(ROOT)),'status':report['status'],'error':report.get('error')},indent=2),flush=True)
    return 0 if report['status']=='pass' else 1


def main():
    configure();p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',action='store_true');p.add_argument('--exclusive-runtime',action='store_true');p.add_argument('--app-only',action='store_true')
    p.add_argument('--guard-exec-json',help=argparse.SUPPRESS)
    p.add_argument('--checkpoint',default='outputs/phase2/learning_pipeline/model/checkpoint.json');p.add_argument('--source-id',default='85c0a48b6a1d47cc8057710e05bf7773')
    p.add_argument('--output',default='outputs/phase2/offline/cold-smoke-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    args=p.parse_args()
    if args.guard_exec_json:guarded_exec(args.guard_exec_json);return 0
    if not args.run:print(json.dumps(plan(args),indent=2));return 0
    return execute(args)


if __name__=='__main__':raise SystemExit(main())
