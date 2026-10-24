"""Actual loopback HTTP actions, saved replay isolation and sandbox persistence."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.cloud import workspace as w


class WorkbenchHttp(unittest.TestCase):
    def setUp(self):
        base=w.local('.tmp/cloud');base.mkdir(parents=True,exist_ok=True)
        self.scratch=tempfile.TemporaryDirectory(dir=base)
        self.server=w.make_server(port=0,simulation_root=Path(self.scratch.name))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3);self.scratch.cleanup()

    def request(self,path,data=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            body=None if data is None else json.dumps(data)
            connection.request('GET' if data is None else 'POST',path,body,headers or ({'Content-Type':'application/json'} if data is not None else {}))
            response=connection.getresponse();payload=response.read()
            return response.status,payload
        finally:connection.close()

    def json(self,path,data=None):
        code,payload=self.request(path,data);self.assertEqual(code,200,payload.decode());return json.loads(payload)

    def test_saved_replay_backward_seek_and_page(self):
        code,page=self.request('/research');self.assertEqual(code,200);self.assertIn('近似模拟器',page.decode())
        catalog=self.json('/api/replay/catalog');self.assertGreater(len(catalog['episodes']),1)
        start=self.json('/api/replay/state');last=self.json('/api/replay/seek',{'index':start['frames']-1})
        self.assertTrue(last['at_last_saved_frame'])
        reset=self.json('/api/replay/seek',{'index':0})
        self.assertEqual(reset['observation']['tick'],start['observation']['tick'])
        self.assertTrue(all(h['tick']<=reset['observation']['tick'] for h in reset['observation']['history']))
        self.assertNotIn('targets',reset['observation'])

    def test_simulator_play_terminal_saved_reload_and_errors(self):
        state=self.json('/api/simulator/reset',{'seed':19,'max_time':2})
        card=min(state['hand'],key=lambda h:h['cost'])
        played=self.json('/api/simulator/play',{'slot':card['slot'],'x':9,'y':25,'seconds':0})
        self.assertTrue(played['feedback']['consumed']);self.assertEqual(played['observation']['time'],0)
        end=self.json('/api/simulator/step',{'seconds':2,'autoplay':True});self.assertTrue(end['done'])
        saved=self.json('/api/simulator/save',{});self.json('/api/simulator/reset',{'seed':20,'max_time':5})
        replay=self.json('/api/simulator/replay',{'run_id':saved['run_id'],'index':0})
        self.assertEqual(replay['state']['seed'],19);self.assertFalse(replay['replay']['active_match_changed'])
        current=self.json('/api/simulator/state');self.assertEqual(current['seed'],20)
        code,_=self.request('/api/simulator/replay',{'run_id':'../escape','index':0});self.assertEqual(code,400)

    def test_post_rejects_invalid_input_without_mutation(self):
        before=self.json('/api/simulator/state')
        status,_=self.request('/api/simulator/step',{'seconds':1},headers={'Content-Type':'application/json','Origin':'https://invalid.example'})
        self.assertEqual(status,403)
        status,_=self.request('/api/simulator/play',{'slot':True,'x':9,'y':25});self.assertEqual(status,400)
        after=self.json('/api/simulator/state');self.assertEqual(before['observation'],after['observation'])

    def test_codespaces_proxy_origin_with_rewritten_local_host(self):
        with patch.dict('os.environ',{'CODESPACES':'true','CODESPACE_NAME':'test-workspace','GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN':'app.github.dev'}):
            origin=f'https://test-workspace-{self.server.server_port}.app.github.dev'
            code,_=self.request('/api/simulator/reset',{'seed':42},headers={'Content-Type':'application/json','Origin':origin})
            self.assertEqual(code,200)
            code,_=self.request('/api/simulator/step',{'seconds':1},headers={'Content-Type':'application/json','Origin':origin.replace('test-workspace','another-workspace')})
            self.assertEqual(code,403)

    def test_explicit_replay_dataset_is_used_without_touching_live_environment(self):
        metadata=w.read(w.manifest()['phase3']['metadata'])
        target=Path(self.scratch.name)/'new-native-export.json'
        w.write(target,metadata)
        server=w.make_server(port=0,replay_metadata=target)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            try:
                connection.request('GET','/api/replay/catalog')
                response=connection.getresponse();catalog=json.loads(response.read())
                self.assertEqual(response.status,200)
            finally:connection.close()
            self.assertEqual(catalog['provenance']['metadata'],target.relative_to(w.ROOT).as_posix())
            self.assertEqual(catalog['native_rpc_calls'],0)
            self.assertTrue(catalog['episodes'])
        finally:server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__':unittest.main()
