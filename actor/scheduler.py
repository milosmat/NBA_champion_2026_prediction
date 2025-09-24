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


class Scheduler(Actor):
    def __init__(self, name, system, teams, train_data, features, imputer, rounds: int = 1, fedprox_mu: float = 0.0):
        super().__init__(name, system)
        # Queues and data
        self.all_teams = list(teams)
        self.teams = list(teams)
        self.train_data = train_data
        self.features = features
        self.imputer = imputer
        # Bookkeeping
        self.active_requests = 0
        self.current_round = 1
        self.total_rounds = int(rounds)
        self.fedprox_mu = float(fedprox_mu)
        self.workers = set()

    async def default_behavior(self, message):
        from actor.scheduler import GiveMeWork, AssignTeam, NoMoreWork, RegisterWorker, WorkDone
        from actor.aggregator import RoundComplete, SetGlobalModel

        if isinstance(message, RegisterWorker):
            # Zapamti gde živi ovaj worker da bismo mogli da mu pošaljemo posao
            self.system.register_peer(message.worker, message.host, message.port)
            self.workers.add(message.worker)
            print(f"[Scheduler] registrovao remote worker {message.worker} @ {message.host}:{message.port}")

        elif isinstance(message, GiveMeWork):
            if self.teams:
                team = self.teams.pop(0)
                # Za mrežu šaljemo samo naziv tima; worker će lokalno iseći svoj train
                self.active_requests += 1
                self.system.tell(message.worker, AssignTeam(team))
                print(f"[Scheduler] dodelio tim {team} → {message.worker}")
            else:
                self.system.tell(message.worker, NoMoreWork())
                print(f"[Scheduler] nema više posla za {message.worker}")
                # Ako je red prazan i nema aktivnih dodela — kraj runde ili kraj ukupno
                if not self.teams and self.active_requests == 0:
                    self.system.tell("aggregator_p2p", RoundComplete(self.current_round, self.total_rounds, self.fedprox_mu))
                    print(f"[Scheduler] Runda {self.current_round}/{self.total_rounds} završena → poslato RoundComplete")
                    # Priprema sledeće runde ili završetak
                    if self.current_round < self.total_rounds:
                        self.current_round += 1
                        self.teams = list(self.all_teams)
                        print(f"[Scheduler] Pokrećem rundu {self.current_round}/{self.total_rounds}")
                        # Kick-off: enqueue GiveMeWork for each registered worker
                        for w in sorted(self.workers):
                            self.system.tell(self.name, GiveMeWork(w))
                    else:
                        print("[Scheduler] Sve runde završene.")

        elif isinstance(message, WorkDone):
            if self.active_requests > 0:
                self.active_requests -= 1
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