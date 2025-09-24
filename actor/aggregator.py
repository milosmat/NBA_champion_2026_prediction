from random import random
from actor.actor_system import Actor
import numpy as np
from sklearn.linear_model import LogisticRegression
from actor.crdt import Increment

# Poruke
class TrainRequest:
    pass

class RegisterTeam:
    def __init__(self, team_actor_name: str, host: str, port: int):
        self.team_actor_name = team_actor_name
        self.host = host
        self.port = int(port)

class ModelUpdate:
    def __init__(self, coef, intercept):
        self.coef = coef
        self.intercept = intercept

class GlobalModel:
    def __init__(self, coef, intercept, round_idx: int | None = None):
        self.coef = coef
        self.intercept = intercept
        self.round_idx = round_idx

class SetGlobalModel:
    def __init__(self, coef, intercept):
        self.coef = coef
        self.intercept = intercept

# === TeamNode ===
class TeamNode(Actor):
    def __init__(self, name, system, data, features, imputer):
        super().__init__(name, system)
        self.data = data
        self.features = features
        self.imputer = imputer

    async def idle_behavior(self, message):
        if isinstance(message, TrainRequest):
            print(f"[{self.name}] prelazi u stanje TRAINING")
            self.become(self.training_behavior)
            await self.training_behavior(message)
            
    async def training_behavior(self, message):
        if isinstance(message, TrainRequest):
            X = self.imputer.transform(self.data[self.features])
            y = self.data["home_win"]
            model = LogisticRegression(max_iter=500)
            model.fit(X, y)
            update = ModelUpdate(model.coef_[0], model.intercept_[0])

            print(f"[{self.name}] završio treniranje, prelazi u stanje FINISHED")
            self.become(self.finished_behavior)
            self.system.tell("aggregator", update)
    # kod za test pucanja aktora
    # async def training_behavior(self, message):
    #     if isinstance(message, TrainRequest):
    #         # Simulacija greške sa 10% šanse
    #         if random() < 0.1:
    #             print(f"[{self.name}] *** GRESKA: actor puca tokom treniranja! ***")
    #             raise Exception("Simulirani crash")

    #         X = self.imputer.transform(self.data[self.features])
    #         y = self.data["home_win"]
    #         model = LogisticRegression(max_iter=500)
    #         model.fit(X, y)
    #         update = ModelUpdate(model.coef_[0], model.intercept_[0])

    #         print(f"[{self.name}] završio treniranje, prelazi u stanje FINISHED")
    #         self.become(self.finished_behavior)
    #         self.system.tell("aggregator", update)
            
    async def finished_behavior(self, message):
        print(f"[{self.name}] je u stanju FINISHED i ignoriše poruku: {message}")

    async def on_start(self):
        print(f"[{self.name}] spreman sa {len(self.data)} mečeva (Idle stanje)")
        self.become(self.idle_behavior)
        # auto-registracija kod aggregatora (provider mod)
        try:
            host, port = self.system.host, self.system.port
            self.system.tell("aggregator", RegisterTeam(self.name, host, port))
        except Exception:
            pass

# === Aggregator ===
class Aggregator(Actor):
    def __init__(self, name, system, team_count):
        super().__init__(name, system)
        self.team_count = int(team_count)
        self.received = []
        self.registered = set()
        self.expected = None  # expected number of updates for current round

    async def default_behavior(self, message):
        if isinstance(message, RegisterTeam):
            self.registered.add(message.team_actor_name)
            self.system.register_peer(message.team_actor_name, message.host, message.port)
            print(f"[Aggregator] registracija tima: {message.team_actor_name} @ {message.host}:{message.port}")

        elif isinstance(message, TrainRequest):
            targets = sorted(list(self.registered))
            if self.team_count > 0:
                self.expected = self.team_count
            else:
                self.expected = len(targets)
            self.received = []
            print(f"[Aggregator] pokreće TrainRequest ka {len(targets)} timova, očekujem {self.expected} update-a")
            for t in targets:
                try:
                    self.system.tell(t, TrainRequest())
                except Exception:
                    pass

        elif isinstance(message, ModelUpdate):
            self.received.append((message.coef, message.intercept))
            exp = self.expected if self.expected is not None else self.team_count
            if exp and len(self.received) >= exp:

                coefs, intercepts = zip(*self.received)
                global_coef = np.mean(coefs, axis=0).reshape(1, -1)
                global_intercept = np.mean(intercepts, axis=0)

                self.system.tell("crdt", Increment())
                self.system.tell("evaluator", GlobalModel(global_coef, global_intercept))
                print(f"[Aggregator] primljeno {len(self.received)}/{exp} → poslat GlobalModel evaluatoru")
                self.received = []
                self.expected = None

    async def on_start(self):
        print("[Aggregator] čeka modele od timova")


# P2P Aggregator koji prikuplja ModelShare i na kraju šalje GlobalModel
class AllDone:
    pass

class RoundComplete:
    def __init__(self, round_idx: int, total_rounds: int, fedprox_mu: float = 0.0):
        self.round_idx = int(round_idx)
        self.total_rounds = int(total_rounds)
        self.fedprox_mu = float(fedprox_mu)

class AggregatorP2P(Actor):
    def __init__(self, name, system):
        super().__init__(name, system)
        self.received = []
        self.last_global = None

    async def default_behavior(self, message):
        from actor.p2p import ModelShare
        if isinstance(message, ModelShare):
            self.received.append((message.coef, message.intercept))
        elif isinstance(message, AllDone):
            if not self.received:
                print("[AggregatorP2P] Nema primljenih modela za agregaciju.")
                return
            coefs, intercepts = zip(*self.received)
            global_coef = np.mean(coefs, axis=0).reshape(1, -1)
            global_intercept = float(np.mean(intercepts, axis=0))
            self.system.tell("crdt", Increment())
            self.system.tell("evaluator", GlobalModel(global_coef, global_intercept, round_idx=None))
            try:
                self.system.tell("scheduler", SetGlobalModel(global_coef, global_intercept))
            except Exception:
                pass
            print("[AggregatorP2P] Poslat GlobalModel evaluatoru (finalni)")
            self.last_global = (global_coef.copy(), global_intercept)
            self.received = []

        elif isinstance(message, RoundComplete):
            if not self.received:
                print(f"[AggregatorP2P] Round {message.round_idx}: nema primljenih modela")
                return
            coefs, intercepts = zip(*self.received)
            avg_coef = np.mean(coefs, axis=0).reshape(1, -1)
            avg_intercept = float(np.mean(intercepts, axis=0))

            if self.last_global is not None and message.fedprox_mu > 0.0:
                prev_coef, prev_intercept = self.last_global
                mu = float(message.fedprox_mu)
                global_coef = (1.0 - mu) * avg_coef + mu * prev_coef
                global_intercept = float((1.0 - mu) * avg_intercept + mu * prev_intercept)
            else:
                global_coef = avg_coef
                global_intercept = avg_intercept

            self.system.tell("crdt", Increment())
            self.system.tell("evaluator", GlobalModel(global_coef, global_intercept, round_idx=message.round_idx))
            try:
                self.system.tell("scheduler", SetGlobalModel(global_coef, global_intercept))
            except Exception:
                pass
            print(f"[AggregatorP2P] Round {message.round_idx}/{message.total_rounds} → poslat GlobalModel evaluatoru")

            self.last_global = (global_coef.copy(), global_intercept)
            self.received = []

    async def on_start(self):
        print("[AggregatorP2P] spreman za prijem lokalnih modela")
