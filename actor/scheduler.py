from actor.actor_system import Actor

# Poruke za koordinaciju posla (mrežno bez slanja DataFrame-ova)
class GiveMeWork:
    def __init__(self, worker: str):
        self.worker = worker  # puno ime aktera koji traži posao (npr. worker_BOS_0)

class AssignTeam:
    def __init__(self, team_name: str):
        self.team_name = team_name

class NoMoreWork:
    pass

class RegisterWorker:
    def __init__(self, worker: str, host: str, port: int):
        self.worker = worker
        self.host = host
        self.port = port

class WorkDone:
    def __init__(self, worker: str):
        self.worker = worker

class SetClusterModels:
    def __init__(self, cluster_models: dict):
        self.cluster_models = cluster_models

class SetTeamClusters:
    def __init__(self, mapping: dict):
        self.mapping = mapping


class Scheduler(Actor):
    def __init__(self, name, system, teams, train_data, features, imputer, rounds: int = 1, fedprox_mu: float = 0.0, async_mode: bool = False):
        super().__init__(name, system)

        self.all_teams = list(teams)
        self.teams = list(teams)
        self.train_data = train_data
        self.features = features
        self.imputer = imputer

        self.active_requests = 0
        self.current_round = 1
        self.total_rounds = int(rounds)
        self.fedprox_mu = float(fedprox_mu)
        self.async_mode = bool(async_mode)
        self.workers = set()
        self.team_to_cluster = {}
        self.cluster_models = None
        self._team_idx = 0  # for async round-robin

    async def default_behavior(self, message):
        from actor.aggregator import RoundComplete, SetGlobalModel

        if isinstance(message, RegisterWorker):
            self.system.register_peer(message.worker, message.host, message.port)
            self.workers.add(message.worker)
            print(f"[Scheduler] registrovao remote worker {message.worker} @ {message.host}:{message.port}")

        elif isinstance(message, GiveMeWork):
            if self.async_mode:
                if not self.all_teams:
                    # nothing to do at all
                    self.system.tell(message.worker, NoMoreWork())
                    print(f"[Scheduler] (async) nema timova za {message.worker}")
                else:
                    team = self.all_teams[self._team_idx]
                    self._team_idx = (self._team_idx + 1) % len(self.all_teams)
                    self.active_requests += 1
                    if self.cluster_models and self.team_to_cluster:
                        cid = self.team_to_cluster.get(team)
                        if cid is not None and cid in self.cluster_models:
                            cm = self.cluster_models[cid]
                            try:
                                self.system.tell(message.worker, SetGlobalModel(cm["coef"], cm["intercept"]))
                            except Exception:
                                pass
                    self.system.tell(message.worker, AssignTeam(team))
                    print(f"[Scheduler] (async) dodelio tim {team} → {message.worker}")
            else:
                if self.teams:
                    team = self.teams.pop(0)
                    self.active_requests += 1
                    if self.cluster_models and self.team_to_cluster:
                        cid = self.team_to_cluster.get(team)
                        if cid is not None and cid in self.cluster_models:
                            cm = self.cluster_models[cid]
                            try:
                                self.system.tell(message.worker, SetGlobalModel(cm["coef"], cm["intercept"]))
                            except Exception:
                                pass
                    self.system.tell(message.worker, AssignTeam(team))
                    print(f"[Scheduler] dodelio tim {team} → {message.worker}")
                else:
                    self.system.tell(message.worker, NoMoreWork())
                    print(f"[Scheduler] nema više posla za {message.worker}")

                    if not self.teams and self.active_requests == 0:
                        self.system.tell("aggregator_p2p", RoundComplete(self.current_round, self.total_rounds, self.fedprox_mu))
                        print(f"[Scheduler] Runda {self.current_round}/{self.total_rounds} završena → poslato RoundComplete")

                        if self.current_round < self.total_rounds:
                            self.current_round += 1
                            self.teams = list(self.all_teams)
                            print(f"[Scheduler] Pokrećem rundu {self.current_round}/{self.total_rounds}")

                            for w in sorted(self.workers):
                                self.system.tell(self.name, GiveMeWork(w))
                        else:
                            print("[Scheduler] Sve runde završene.")

        elif isinstance(message, WorkDone):
            if self.active_requests > 0:
                self.active_requests -= 1
            if not self.async_mode:
                if not self.teams and self.active_requests == 0:
                    self.system.tell("aggregator_p2p", RoundComplete(self.current_round, self.total_rounds, self.fedprox_mu))
                    print(f"[Scheduler] Runda {self.current_round}/{self.total_rounds} završena (WorkDone) → RoundComplete")
                    if self.current_round < self.total_rounds:
                        self.current_round += 1
                        self.teams = list(self.all_teams)
                        print(f"[Scheduler] Pokrećem rundu {self.current_round}/{self.total_rounds}")
                        for w in sorted(self.workers):
                            self.system.tell(self.name, GiveMeWork(w))
                    else:
                        print("[Scheduler] Sve runde završene.")

        elif isinstance(message, SetGlobalModel):
            for w in sorted(self.workers):
                self.system.tell(w, message)
        elif isinstance(message, SetClusterModels):
            self.cluster_models = message.cluster_models or {}
            for w in sorted(self.workers):
                pass
        elif isinstance(message, SetTeamClusters):
            self.team_to_cluster = dict(message.mapping) if message.mapping else {}