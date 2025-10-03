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
    def __init__(self, sender, coef, intercept):
        self.sender = sender
        self.coef = coef
        self.intercept = intercept

class TeamNodeP2P(Actor):
    def __init__(self, name, system, data, features, imputer, total_rounds: int = 1, eval_after: bool = False):
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

        # state po rundi
        self.local_coef = None
        self.local_intercept = None
        self.collected = {}
        self.ready = set()

    async def default_behavior(self, message):
        if isinstance(message, PeerList):
            self.peers = message.peers
            self.is_reporter = message.is_reporter
            self.reporter_name = message.reporter_name
            self.total_rounds = int(message.total_rounds)
            print(f"[{self.name}] P2P peers set ({len(self.peers)}), reporter={self.is_reporter}, rounds={self.total_rounds}")
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
            share = ModelShare(self.name, self.local_coef, self.local_intercept)
            for p in self.peers:
                self.system.tell(p, share)


        elif isinstance(message, ModelShare):
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
            if self.is_reporter:
                self.ready.add(message.peer_name)
                expected = set([self.name] + self.peers)
                if expected.issubset(self.ready):
                    await self._start_next_round()

    async def on_start(self):
        print(f"[{self.name}] P2P node spreman sa {len(self.data)} mečeva")

    async def _start_next_round(self):
        for p in self.peers:
            self.system.tell(p, StartRound())
        self.system.tell(self.name, StartRound())
