"""Execute imported public events through the existing native RPC owner.

Public event times are target execution hypotheses, not original native
registration receipts. New registrations and consumption feedback belong to
this local run. No teacher or substitute physics is used by this controller.
"""
from copy import deepcopy
import threading

from .reconstruction import align_initial_deal


class CloudSourceReplay:
    """A controller called only inside CloudNativeSession's worker and lease."""

    def __init__(self, engine, plan, *, max_tick=7200, progress_callback=None,
                 stop_requested=None, message_callback=None, lease_external=False):
        from .public_replay import PublicReplayPlan
        if not lease_external:
            raise ValueError('Public replay requires the existing session native lease')
        self.engine = engine
        self.plan = plan if isinstance(plan, PublicReplayPlan) else PublicReplayPlan.from_dict(plan)
        self.commands = deepcopy(self.plan.commands)
        if type(max_tick) is not int or max_tick < 100:
            raise ValueError('Invalid public replay watchdog tick')
        if self.commands and self.commands[-1]['tick'] > max_tick:
            raise ValueError('来源动作超出当前原生时钟预算，请提高 max_tick 后重新打开')
        self.end_tick = max_tick
        self.source_end_tick = max((c['tick'] for c in self.commands), default=90)
        self.progress_callback = progress_callback
        self.message_callback = message_callback
        self.progress_message = None
        self.phase = 'not_started'
        self.stop_requested = stop_requested
        self._owner = threading.get_ident()
        self.state = None
        self.error = None
        self.valid = False
        self.runs = []
        self.seek_history = []
        self.cursor = 0
        self.feedback = []
        self._feedback_ids = set()
        self.last_seek_interrupted = False
        self.requested_seek_tick = None

    def _require_owner(self, state=True):
        if self._owner != threading.get_ident():
            raise RuntimeError('Public replay RPC requires the session lease-owning worker thread')
        if state and (not self.valid or self.state is None):
            raise RuntimeError('来源对局无效或尚未初始化；请显式重新打开来源')

    def _stop_pending(self):
        return bool(self.stop_requested and self.stop_requested())

    def _failed(self, exc):
        self.valid = False
        self.phase = 'failed'
        self.error = f'{type(exc).__name__}: {exc}; no automatic mutation retry'
        if self.runs:
            self.runs[-1]['error'] = self.error

    def _publish_state(self, state):
        self.state = deepcopy(state)
        if self.state['generation'] != self.generation:
            raise RuntimeError('Another controller reset the public replay generation')
        if self.progress_callback:
            self.progress_callback(deepcopy(self.state))

    def start(self, folder):
        """Reset and align the fixed public-action-compatible initial deal."""
        self._require_owner(state=False)
        self.valid = False
        self.error = None
        self.state = None
        self.cursor = 0
        self.feedback = []
        self._feedback_ids = set()
        self.last_seek_interrupted = False
        self.requested_seek_tick = None
        self.phase = 'initializing'
        self.runs.append(dict(run_index=len(self.runs)+1, registrations=[], error=None))
        try:
            self.progress_message = '正在创建原生场景并对齐初始手牌；等待场景加载和 tick 90 预热，尚无可用对局状态。'
            if self.message_callback:
                self.message_callback(self.progress_message)
            opening = self.plan.opening_cards_hypothesis
            cycle = self.plan.cycle_hypothesis
            # JSON object keys are strings after a saved-plan round trip.
            def owners(values):
                return [deepcopy(values[o] if not isinstance(values, dict) or o in values
                                 else values[str(o)]) for o in (0, 1)]
            hypothesis = dict(opening_cards_hypothesis=owners(opening),
                              cycle_hypothesis=owners(cycle))
            state, configured, attempts = align_initial_deal(
                self.engine, deepcopy(self.plan.match), hypothesis, folder)
            self.initialization_tick = state['tick']
            if self.commands and self.commands[0]['tick'] <= self.initialization_tick:
                raise ValueError('来源出牌早于原生可操作边界；未移动或丢弃来源动作')
            self.generation = state['generation']
            self.runs[-1].update(initialization_attempts=len(attempts),
                                 configured_match=deepcopy(configured))
            self.valid = True
            self.phase = 'ready'
            self._publish_state(state)
            self.progress_message = f'初始手牌与轮转已对齐（{len(attempts)} 次显式初始化）；正在读取 Android 原生画面。'
            if self.message_callback:
                self.message_callback(self.progress_message)
            return deepcopy(self.state)
        except BaseException as exc:
            self._failed(exc)
            raise

    def _record_feedback(self):
        for command in self.commands[:self.cursor]:
            request_id = command['request_id']
            if command['tick'] > self.state['tick'] or request_id in self._feedback_ids:
                continue
            if self._stop_pending():
                return False
            replies = self.engine.execution_feedback([request_id])
            if len(replies) != 1 or replies[0].get('request_id') != request_id:
                raise RuntimeError('原生执行反馈没有唯一覆盖来源动作')
            row = deepcopy(replies[0])
            row.update(source_event_index=command['source_event_index'],
                       source_command=deepcopy(command),
                       registration_evidence_origin='this_local_execution',
                       original_source_registration_tick=None)
            self.feedback.append(row)
            self._feedback_ids.add(request_id)
        return True

    def _register_current(self):
        while self.cursor < len(self.commands):
            command = self.commands[self.cursor]
            registration_tick = command['tick']-1
            if registration_tick > self.state['tick']:
                break
            if registration_tick != self.state['tick']:
                raise RuntimeError('原生推进错过了来源动作的计划登记边界')
            if self._stop_pending():
                return False
            receipt = self.engine.schedule_card(command['owner'], command['card_id'],
                command['x'], command['y'], command['tick'], request_id=command['request_id'])
            # This is a newly observed receipt, never a reconstructed source receipt.
            self.runs[-1]['registrations'].append(dict(source_event_index=command['source_event_index'],
                source_command=deepcopy(command), local_registration=deepcopy(receipt),
                original_source_registration_tick=None))
            self.cursor += 1
            observed = receipt.get('registration_receipt', {}).get('registeredAtTick')
            if observed != registration_tick:
                raise RuntimeError('本次原生登记回执未处于计划的目标 tick 前一刻')
        return True

    def seek(self, tick):
        self._require_owner()
        if type(tick) is not int or not self.initialization_tick <= tick <= self.end_tick:
            raise ValueError('Invalid source seek tick')
        if tick < self.state['tick']:
            raise ValueError('Backward source seek needs an explicit reset into a new archive')
        before = self.state['tick']
        self.requested_seek_tick = tick
        self.last_seek_interrupted = False
        try:
            while True:
                if self._stop_pending():
                    self.last_seek_interrupted = True
                    break
                if not self._record_feedback():
                    self.last_seek_interrupted = True
                    break
                if self.state['tick'] == tick or self.state.get('finalized'):
                    break
                if not self._register_current():
                    self.last_seek_interrupted = True
                    break
                if self._stop_pending():
                    self.last_seek_interrupted = True
                    break
                current = self.state['tick']
                boundaries = [tick]
                if self.cursor < len(self.commands):
                    boundaries.append(self.commands[self.cursor]['tick']-1)
                boundaries.extend(c['tick'] for c in self.commands[:self.cursor] if c['tick'] > current)
                target = min(t for t in boundaries if t > current)
                state = self.engine.advance_exact_ticks(target-current)
                if not current < state['tick'] <= target or (state['tick'] != target and not state['finalized']):
                    raise RuntimeError('原生时钟未到达来源执行边界或自然终局')
                self._publish_state(state)
            self.seek_history.append(dict(before_tick=before, target_tick=tick,
                after_tick=self.state['tick'], target_reached=self.state['tick'] == tick,
                interrupted=self.last_seek_interrupted, run_index=len(self.runs)))
            return deepcopy(self.state)
        except BaseException as exc:
            self._failed(exc)
            raise

    def step(self, ticks=1):
        self._require_owner()
        if type(ticks) is not int or ticks < 1:
            raise ValueError('Invalid public replay step')
        return self.seek(min(self.state['tick']+ticks, self.end_tick))

    def status(self):
        feedback = deepcopy(self.feedback)
        return dict(mode='public_source_reconstruction', valid=self.valid, error=self.error,
            phase=self.phase, status_message=self.progress_message,
            tick=self.state['tick'] if self.state else None,
            generation=self.state['generation'] if self.state else None,
            initialization_tick=getattr(self, 'initialization_tick', 90), end_tick=self.end_tick,
            source_end_tick=self.source_end_tick, source_actions=len(self.commands),
            registered_actions=self.cursor, executed_actions=len(feedback),
            card_consumed=sum(row.get('card_consumed') is True for row in feedback),
            native_injected=sum(row.get('native_injected') is True for row in feedback),
            not_confirmed=sum(row.get('card_consumed') is not True for row in feedback),
            remaining_actions=len(self.commands)-self.cursor,
            source_actions_finished=len(feedback) == len(self.commands),
            natural_terminal=bool(self.state and self.state.get('finalized')),
            assumptions=deepcopy(self.plan.assumptions),
            source_summary=deepcopy(self.plan.source_summary),
            last_seek_interrupted=self.last_seek_interrupted,
            requested_seek_tick=self.requested_seek_tick, runs=len(self.runs),
            seek_mode='reset_and_replay', registration_evidence_origin='this_local_execution',
            original_source_registration_tick=None)

    def report(self):
        return dict(schema='royale-cloud-source-execution.v1', current=self.status(),
            source_plan=self.plan.to_dict(), run=deepcopy(self.runs[-1]) if self.runs else None,
            feedback=deepcopy(self.feedback), seek_history=deepcopy(self.seek_history),
            original_source_registration_ticks_known=False,
            official_fidelity_verified=False,
            scope='Source event order/time/coordinates drive Android; local receipts and consumption are recorded separately')

    def close(self):
        if self._owner is not None:
            self._require_owner(state=False)
            self._owner = None
            self.valid = False
