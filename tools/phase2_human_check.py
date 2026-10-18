"""Bounded protocol/native smoke test for an already running Human console.

No browser automation; never retries a mutation. Stream frames are asynchronous
display evidence, not exact-tick training labels. All evidence stays in project.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,local
from royale_phase2.viewer import write_json
from royale_phase2.platform_stream import JPEGParser
configure()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8770);parser.add_argument('--output',required=True)
    parser.add_argument('--shutdown',action='store_true');parser.add_argument('--stream-seconds',type=int,default=12)
    args=parser.parse_args();folder=local(args.output);folder.mkdir(parents=True,exist_ok=False)
    base=f'http://127.0.0.1:{args.port}'; report={'status':'failed','checks':[]}; started=time.monotonic()
    stop=threading.Event();thread=None;last_jpeg=[None]
    video={'frames':0,'bytes':0,'changed_frames':0,'arrival_times':[]}
    def get(path):return urllib.request.urlopen(base+path,timeout=10).read()
    def state():return json.loads(get('/api/state'))
    try:
        # The wrapper prepares the VM before binding HTTP. Retry only this
        # read-only readiness fetch, never any card/control mutation.
        ready_deadline=time.monotonic()+240
        while True:
            try: page=get('/').decode();break
            except (OSError,urllib.error.URLError):
                if time.monotonic()>=ready_deadline:raise
                time.sleep(.5)
        (folder/'served.html').write_text(page,encoding='utf8')
        token=json.loads(re.search(r'const token\s*=\s*("[^"]+")',page).group(1))
        def control(command,expect_error=False,**data):
            request=urllib.request.Request(base+'/api/control',data=json.dumps({'command':command,**data}).encode(),
                headers={'Content-Type':'application/json','X-Platform-Token':token})
            ticket=json.loads(urllib.request.urlopen(request,timeout=10).read())['ticket']
            if command=='shutdown':return
            end=time.monotonic()+90
            while time.monotonic()<end:
                s=state();job=s.get('jobs',{}).get(ticket,{})
                if job.get('status')=='error':
                    if expect_error:return job
                    raise RuntimeError(job)
                if job.get('status')=='done':
                    assert not expect_error,'Expected a rejected action'
                    return job
                if s.get('error'):raise RuntimeError(s['error'])
                time.sleep(.15)
            raise TimeoutError('Control did not finish; action not retried: '+command)
        end=time.monotonic()+240
        while time.monotonic()<end:
            initial=state()
            if initial.get('error'):raise RuntimeError(initial['error'])
            if initial.get('ready'):break
            time.sleep(.5)
        else:raise TimeoutError('Human initialization')
        assert initial['mode']=='human' and not initial['playing'] and initial['human']['owner']==1 and initial['ai']['owner']==0
        write_json(folder/'initial_state.json',initial);report['session_folder']=initial['folder']
        report['checks'].append('human_bottom_ai_top_ready_paused')
        stream_deadline=time.monotonic()+35
        while time.monotonic()<stream_deadline:
            current=state();stream=current.get('stream',{})
            if stream.get('available'):break
            if stream.get('error'):raise RuntimeError(stream['error'])
            time.sleep(.2)
        else:raise TimeoutError('Native video stream did not produce its first frame')
        def read_stream():
            previous=None;parser=JPEGParser();clock=time.monotonic()
            try:
                with urllib.request.urlopen(base+'/api/stream.mjpg',timeout=10) as response:
                    assert 'boundary=frame' in response.headers['Content-Type']
                    while not stop.is_set() and time.monotonic()-clock<max(8,args.stream_seconds)+90:
                        data=response.read(16384)
                        if not data:break
                        video['bytes']+=len(data)
                        for jpeg in parser.feed(data):
                            digest=hashlib.sha256(jpeg).hexdigest();video['frames']+=1
                            video['arrival_times'].append(time.monotonic())
                            video['changed_frames']+=digest!=previous;previous=digest
                            if video['frames']==1:(folder/'stream_first.jpg').write_bytes(jpeg)
                            last_jpeg[0]=jpeg
            except Exception as exc:video['error']=str(exc)
        thread=threading.Thread(target=read_stream,daemon=True);thread.start()
        before=initial['tick']; human=initial['human']
        card=next(card for card in human['hand'] if card['affordable'] and card['card']<28000000)
        u=(111+3500/18000*856)/1080;v=(436+21500/32000*1230)/1920
        control('human_turn',slot=card['slot'],card=card['card'],generation=human['generation'],u=u,v=v)
        turn=state();write_json(folder/'after_teaching_turn.json',turn)
        assert turn['tick']==before+20 and not turn['playing']
        action=turn['human']['last_action'];sequence=action['registration']['request_id']
        assert any(item['request_id']==sequence and item['card_consumed'] is True for item in turn['feedback'])
        decision=turn['ai']['last_decision']
        assert decision['tick']==before+1 and decision['policy_reason']
        assert any(event['owner']==1 and event['tick']==before+1 and event['native_card_id']==card['card']
                   for event in decision['policy_input']['history'])
        report['checks']+=['human_actual_consumption','ai_input_contains_already_consumed_human_play',
                           'ai_actual_rule_reason_logged','teaching_turn_advances_exact20_and_pauses']
        invalid=turn['human']['hand'][0]
        control('human_turn',expect_error=True,slot=invalid['slot'],card=999,generation=human['generation'],u=u,v=v)
        assert state()['tick']==turn['tick'];report['checks'].append('stale_card_rejected_without_advance')
        start_continuous=time.monotonic();start_tick=turn['tick']; control('play')
        while time.monotonic()-start_continuous<40:
            active=state()
            if active['tick']>=start_tick+20:break
            time.sleep(.2)
        else:raise TimeoutError('Continuous native time did not advance')
        card=next(card for card in active['human']['hand'] if card['affordable'] and card['card']<28000000)
        control('human_play',slot=card['slot'],card=card['card'],generation=active['human']['generation'],x=14500,y=23500)
        while time.monotonic()-start_continuous<60:
            active=state()
            if active['tick']>=start_tick+80 or active['terminal']:break
            time.sleep(.2)
        else:raise TimeoutError('Continuous run failed to reach the bounded test target')
        control('pause');control('report');final=state();write_json(folder/'final_state.json',final)
        assert not final['playing'] and final['tick']>start_tick
        assert all(any(row['request_id']==action['registration']['request_id'] and row['card_consumed'] is True
                       for row in final['feedback']) for action in (turn['human']['last_action'],final['human']['last_action']))
        report['checks']+=['continuous_native_advance_and_pause','continuous_human_play_consumed']
        report['continuous']={'native_ticks':final['tick']-start_tick,
                              'wall_seconds':time.monotonic()-start_continuous}
        while time.monotonic()-started<args.stream_seconds:
            if video.get('error'):raise RuntimeError(video['error'])
            time.sleep(.3)
        stop.set();thread.join(12)
        if last_jpeg[0] is not None:(folder/'stream_last.jpg').write_bytes(last_jpeg[0])
        times=video.pop('arrival_times'); assert video['frames']>=5 and video['changed_frames']>=3,video
        assert not video.get('error'),video
        video['observed_http_fps']=(len(times)-1)/(times[-1]-times[0]) if len(times)>1 else 0
        video['sampling']='Actual HTTP MJPEG arrival times; JPEG has no native tick binding'
        report['stream']=video;report['checks'].append('real_h264_to_http_mjpeg_multiple_changed_frames')
        report.update(status='pass',initial_tick=before,last_tick=final['tick'],native_terminal=final['terminal'],
                      decisions=final['decision_count'],native_events=final['actual_native_events'],
                      confirmed_consumptions=sum(row.get('card_consumed') is True for row in final['feedback']))
        if args.shutdown:control('shutdown');report['checks'].append('shutdown_submitted')
    except Exception as exc:report.update(error=str(exc),traceback=traceback.format_exc())
    finally:
        stop.set()
        if thread:thread.join(3)
        report['wall_seconds']=time.monotonic()-started;write_json(folder/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['status']=='pass' else 1


if __name__=='__main__':raise SystemExit(main())
