from actor.actor_system import Actor
import numpy as np
from sklearn.linear_model import LogisticRegression
from math import ceil
from actor.aggregator import GlobalModel
from actor.crdt import Increment

class StartRound: pass
class PeerList:
    def __init__(self, peers, is_reporter=False, reporter_name: str | None = None, total_rounds: int = 1):
        self.peers = peers
        self.is_reporter = is_reporter
        self.reporter_name = reporter_name
        self.total_rounds = int(total_rounds)
class PeerReady:
    def __init__(self, peer_name: str):
        self.peer_name = peer_name
class ModelShare:
    def __init__(self, sender, coef, intercept, version: int | None = None, ts_ms: int | None = None):
        self.sender = sender
        self.coef = coef
        self.intercept = intercept
        self.version = version
        self.ts_ms = ts_ms

class TeamNodeP2P(Actor):
    def __init__(self, name, system, data, features, imputer, total_rounds: int = 1, eval_after: bool = False,
                 gossip_async: bool = False, gossip_batch: int = 3, gossip_window_ms: int = 2000, gossip_interval_ms: int = 2000, staleness_alpha: float = 0.0,
                 gossip_max_flushes: int = 0, gossip_max_seconds: int = 0, gossip_eval_on_stop: bool = False,
                 gossip_converge_eps: float = 0.0, gossip_converge_patience: int = 3):
        super().__init__(name, system)
        self.data = data
        self.features = features
        self.imputer = imputer
        self.peers = []
        self.is_reporter = False
        self.reporter_name = None
        self.total_rounds = int(total_rounds)
        self.current_round = 0
        self.eval_after = bool(eval_after)
        self.gossip_async = bool(gossip_async)
        self.gossip_batch = max(1, int(gossip_batch))
        self.gossip_window_ms = max(0, int(gossip_window_ms))
        self.gossip_interval_ms = max(0, int(gossip_interval_ms))
        self.staleness_alpha = float(staleness_alpha)
        self.gossip_max_flushes = int(gossip_max_flushes)
        self.gossip_max_seconds = int(gossip_max_seconds)
        self.gossip_eval_on_stop = bool(gossip_eval_on_stop)
        self.gossip_converge_eps = float(gossip_converge_eps)
        self.gossip_converge_patience = int(max(1, gossip_converge_patience))

        # state po rundi
        self.local_coef = None
        self.local_intercept = None
        self.collected = {}
        self._share_version = 0
        self._last_flush_ms = 0
        self._buffer = {}  # sender -> (coef, intercept, ver, ts)
        self._seen = {}    # sender -> last_version
        self._flush_count = 0
        self._start_ms = None
        self._prev_global_coef = None
        self._prev_global_intercept = None
        self._consec_below_eps = 0
        self.ready = set()

    async def default_behavior(self, message):
        if isinstance(message, PeerList):
            self.peers = message.peers
            self.is_reporter = message.is_reporter
            self.reporter_name = message.reporter_name
            self.total_rounds = int(message.total_rounds)
            print(f"[{self.name}] P2P peers set ({len(self.peers)}), reporter={self.is_reporter}, rounds={self.total_rounds}")
            if self.gossip_async:
                # continuous mode: reporter ne koristi PeerReady barijeru
                if self.is_reporter:
                    # nothing to do; reporter samo flushuje prozore
                    pass
                # svi periodično šalju share
                await self._schedule_periodic_share()
            else:
                if self.is_reporter:
                    self.ready.add(self.name)
                    if not self.peers:
                        await self._start_next_round()
                else:
                    # notify reporter this node is ready
                    target = self.reporter_name if self.reporter_name else (self.peers[0] if self.peers else None)
                    if target:
                        self.system.tell(target, PeerReady(self.name))

        elif isinstance(message, StartRound):
            # 1) lokalni trening
            X = self.imputer.transform(self.data[self.features])
            y = self.data["home_win"]
            model = LogisticRegression(max_iter=500)
            model.fit(X, y)
            self.local_coef = model.coef_[0]
            self.local_intercept = model.intercept_[0]
            self.collected = {self.name: (self.local_coef, self.local_intercept)}

            # 2) broadcast moje težine
            share = ModelShare(self.name, self.local_coef, self.local_intercept, version=self._share_version)
            for p in self.peers:
                self.system.tell(p, share)


        elif isinstance(message, ModelShare):
            if self.gossip_async:
                # dedup by version
                last_v = self._seen.get(message.sender, -1)
                ver = message.version if message.version is not None else last_v + 1
                if ver <= last_v:
                    return
                self._seen[message.sender] = ver
                # buffer update
                self._buffer[message.sender] = (message.coef, message.intercept, ver, message.ts_ms)
                await self._maybe_flush_async()
                return

            self.collected[message.sender] = (message.coef, message.intercept)

            expected = set([self.name] + self.peers)
            if expected.issubset(set(self.collected.keys())):
                coefs = np.array([w for (w, b) in self.collected.values()])
                intercepts = np.array([b for (w, b) in self.collected.values()])
                global_coef = np.mean(coefs, axis=0).reshape(1, -1)
                global_intercept = float(np.mean(intercepts, axis=0))

                if self.is_reporter:
                    self.system.tell("evaluator", GlobalModel(global_coef, global_intercept))
                    self.system.tell("crdt", Increment())
                    self.current_round += 1
                    print(f"[{self.name}] (reporter) poslao globalni model evaluatoru (round {self.current_round}/{self.total_rounds})")
                    if self.current_round < self.total_rounds:
                        await self._start_next_round()
                    else:
                        if self.eval_after:
                            try:
                                from actor.evaluator import EvalRequest
                                self.system.tell("evaluator", EvalRequest(pairs=None, best_of=7, reply_to=None, round_idx=self.current_round))
                            except Exception:
                                pass
                else:
                    print(f"[{self.name}] izračunao global (lokalno), reporter će poslati")

        elif isinstance(message, PeerReady):
            if not self.gossip_async and self.is_reporter:
                self.ready.add(message.peer_name)
                expected = set([self.name] + self.peers)
                if expected.issubset(self.ready):
                    await self._start_next_round()

    async def on_start(self):
        print(f"[{self.name}] P2P node spreman sa {len(self.data)} mečeva")
        if self.gossip_async:
            # kick off initial local train + share
            import time
            self._start_ms = int(time.time() * 1000)
            await self._send_periodic_share()

    async def _start_next_round(self):
        for p in self.peers:
            self.system.tell(p, StartRound())
        self.system.tell(self.name, StartRound())

    async def _schedule_periodic_share(self):
        # simple periodic scheduler using asyncio
        async def loop():
            while True:
                if not self.alive:
                    break
                await self._send_periodic_share()
                if self.gossip_interval_ms <= 0:
                    break
                await self._sleep_ms(self.gossip_interval_ms)
        import asyncio
        asyncio.create_task(loop())

    async def _send_periodic_share(self):
        # local train every interval; bump version and broadcast
        X = self.imputer.transform(self.data[self.features])
        y = self.data["home_win"]
        model = LogisticRegression(max_iter=500)
        model.fit(X, y)
        self.local_coef = model.coef_[0]
        self.local_intercept = model.intercept_[0]
        self._share_version += 1
        share = ModelShare(self.name, self.local_coef, self.local_intercept, version=self._share_version)
        for p in self.peers:
            self.system.tell(p, share)

    async def _maybe_flush_async(self):
        # Reporter agregira po batch/window, ostali ne flushuju
        if not self.is_reporter:
            return
        import time
        now = int(time.time() * 1000)
        should_flush = len(self._buffer) >= self.gossip_batch or (self.gossip_window_ms > 0 and (now - self._last_flush_ms) >= self.gossip_window_ms)
        if not should_flush:
            return
        self._last_flush_ms = now
        # staleness weighting: newer versions have higher weight
        if not self._buffer:
            return
        coefs = []
        ints = []
        weights = []
        max_ver = max(v for (_, _, v, _) in self._buffer.values())
        for (_s, (coef, intercept, ver, ts)) in self._buffer.items():
            if self.staleness_alpha > 0.0:
                age = max_ver - ver
                w = 1.0 / (1.0 + self.staleness_alpha * age)
            else:
                w = 1.0
            coefs.append(coef)
            ints.append(intercept)
            weights.append(w)
        W = np.array(weights, dtype=float)
        W = W / (W.sum() if W.sum() > 0 else 1.0)
        C = np.array(coefs)
        I = np.array(ints)
        # weighted average
        gcoef = (W[:, None] * C).sum(axis=0).reshape(1, -1)
        gint = float((W * I).sum())
        self.system.tell("evaluator", GlobalModel(gcoef, gint))
        self.system.tell("crdt", Increment())
        self._flush_count += 1
        # convergence check
        if self.gossip_converge_eps > 0.0:
            converged = False
            try:
                import numpy as _np
                # prev from last_global (if not dict)
                if self._prev_global_coef is None:
                    self._prev_global_coef = gcoef.copy()
                    self._prev_global_intercept = float(gint)
                else:
                    dcoef = float(_np.linalg.norm(self._prev_global_coef - gcoef))
                    dint = abs(float(self._prev_global_intercept - gint))
                    if (dcoef + dint) <= self.gossip_converge_eps:
                        self._consec_below_eps += 1
                    else:
                        self._consec_below_eps = 0
                    self._prev_global_coef = gcoef.copy()
                    self._prev_global_intercept = float(gint)
                    if self._consec_below_eps >= self.gossip_converge_patience:
                        converged = True
            except Exception:
                pass
            if converged:
                await self._stop_async_gossip()
                return
        # check stop conditions
        if self.gossip_max_flushes > 0 and self._flush_count >= self.gossip_max_flushes:
            await self._stop_async_gossip()
            return
        if self.gossip_max_seconds > 0 and self._start_ms is not None:
            elapsed = (now - self._start_ms) // 1000
            if elapsed >= self.gossip_max_seconds:
                await self._stop_async_gossip()
                return
        # clear buffer after flush
        self._buffer.clear()

    async def _sleep_ms(self, ms: int):
        import asyncio
        await asyncio.sleep(max(0.0, ms / 1000.0))

    async def _stop_async_gossip(self):
        print(f"[{self.name}] Gossip-async stop triggered (flushes={self._flush_count})")
        # Optional final evaluation
        if self.gossip_eval_on_stop and self.is_reporter:
            try:
                from actor.evaluator import EvalRequest
                self.system.tell("evaluator", EvalRequest(pairs=None, best_of=7, reply_to=None, round_idx=None))
            except Exception:
                pass
        # Stop this actor
        self.alive = False
