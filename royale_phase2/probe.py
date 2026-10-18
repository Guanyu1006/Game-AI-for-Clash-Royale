"""Bounded local TCP transport for the pinned FirstLight/Jason probe protocol.

This client does not implement battle physics. Connection or content failure is
an error; it never substitutes royale_lab. Mutating commands are never retried.
"""
import ipaddress
import json
import socket

class ProbeUnavailable(RuntimeError):
    pass

class ProbeClient:
    def __init__(self,host='127.0.0.1',port=26789,timeout=15,max_reply=8*1024*1024):
        if host=='localhost':host='127.0.0.1'
        try:loopback=ipaddress.ip_address(host).is_loopback
        except ValueError:loopback=False
        if not loopback:raise ValueError('Only a local native probe or local VM forward is permitted')
        if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid local probe port')
        if timeout<=0 or not 1<=max_reply<=64*1024*1024:raise ValueError('Invalid transport bounds')
        self.address=(host,port);self.timeout=timeout;self.max_reply=max_reply
        self.connection=None;self.stream=None;self.valid=False

    def close(self):
        if self.stream:self.stream.close()
        if self.connection:self.connection.close()
        self.stream=self.connection=None;self.valid=False

    def _receive(self):
        line=self.stream.readline(self.max_reply+1)
        if not line or len(line)>self.max_reply or not line.endswith(b'\n'):
            raise ProbeUnavailable('Incomplete or over-limit native response; request outcome unknown')
        return json.loads(line,parse_constant=lambda x:(_ for _ in ()).throw(ValueError(f'Non-finite JSON: {x}')))

    def connect(self):
        if self.connection:return
        try:
            self.connection=socket.create_connection(self.address,timeout=self.timeout)
            self.stream=self.connection.makefile('rb')
            self.connection.sendall(b'session-v1\n')
            greeting=self._receive()
            if not isinstance(greeting,dict) or greeting.get('ok') is not True:
                raise ProbeUnavailable(f'Native probe session not accepted: {greeting}')
            self.valid=True
        except Exception:
            self.close();raise

    def request(self,command):
        if not isinstance(command,str) or not command or '\n' in command or '\r' in command:
            raise ValueError('A command must be exactly one protocol line')
        try:
            self.connect()
            self.connection.sendall(command.encode('utf-8')+b'\n')
            response=self._receive()
            if isinstance(response,dict) and response.get('ok') is False:
                raise ProbeUnavailable(f'Native command rejected: {response.get("error")}')
            return response
        except Exception:
            self.close();raise

    def status(self):return self.request('status')

    def configure(self,match,rendered=False):
        prefix='configure-native' if rendered else 'configure'
        return self.request(prefix+' '+json.dumps(match,separators=(',',':'),allow_nan=False))

    def advance(self,ticks,rendered=False):
        if type(ticks) is not int or not 1<=ticks<=1_000_000:raise ValueError('Invalid exact tick count')
        return self.request(('advance-native' if rendered else 'step')+f' {ticks}')

    def capture(self,generation=0,cursor=0):
        if any(type(x) is not int or x<0 for x in (generation,cursor)):raise ValueError('Invalid capture cursor')
        return self.request(f'minimal {generation} {cursor}')

    def schedule_card(self,owner,card,x,y,tick):
        if any(type(v) is not int for v in (owner,card,x,y,tick)) or owner not in (0,1) or tick<0:
            raise ValueError('Native command coordinates/time must use verified integer units')
        return self.request(f'replay-schedule-card {owner} {card} {x} {y} {tick}')

    def schedule_status(self,sequence):
        if type(sequence) is not int or sequence<0:raise ValueError('Invalid execution receipt sequence')
        return self.request(f'replay-schedule-status {sequence}')

    def pause(self):return self.request('pause')
    def resume(self):return self.request('resume')

def inspect_probe(host='127.0.0.1',port=26789):
    client=ProbeClient(host,port,timeout=3)
    try:
        status=client.status()
        return {'reachable':True,'status':status,'native_battle_execution_verified':False,
                'scope':'Transport/status inspection only; actual reset/play/terminal probe is required.'}
    except (OSError,ValueError,ProbeUnavailable) as exc:
        return {'reachable':False,'error':f'{type(exc).__name__}: {exc}',
                'native_battle_execution_verified':False,'fallback_used':False}
    finally:client.close()
