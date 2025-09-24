# actor/worker.py
from actor.actor_system import Actor
from actor.scheduler import GiveMeWork, AssignTeam, NoMoreWork, RegisterWorker, WorkDone
from actor.p2p import ModelShare
from actor.aggregator import SetGlobalModel
from actor.health import HealthPing, HealthAck, CrashMe
import numpy as np
from sklearn.linear_model import LogisticRegression


class TeamNodeWorker(Actor):
    def __init__(self, name, system, features, imputer, scheduler_name, train_df=None, fedprox_mu: float = 0.0):
        super().__init__(name, system)
        self.features = features
        self.imputer = imputer
        self.scheduler = scheduler_name
        self.train_df = train_df
        self.global_coef = None
        self.global_intercept = None
        self.fedprox_mu = float(fedprox_mu)

    # --- FedProx helpers (numpy) ---
    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        z = np.clip(z, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(-z))

    def _train_fedprox(self, X: np.ndarray, y: np.ndarray, mu: float,
                        w_global: np.ndarray | None, b_global: float | None,
                        epochs: int = 100, lr: float = 0.1, l2: float = 0.0) -> tuple[np.ndarray, float]:
        n, d = X.shape
        if w_global is None:
            w = np.zeros(d, dtype=float)
            b = 0.0
        else:
            w = np.array(w_global, dtype=float).ravel().copy()
            b = float(b_global if b_global is not None else 0.0)
        yv = np.array(y, dtype=float)
        for _ in range(max(1, int(epochs))):
            z = X.dot(w) + b
            p = self._sigmoid(z)
            # gradients
            diff = (p - yv)
            grad_w = (X.T @ diff) / n + (l2 * w)
            grad_b = float(np.sum(diff) / n)
            if mu > 0.0 and w_global is not None:
                grad_w += mu * (w - np.array(w_global, dtype=float).ravel())
                grad_b += mu * (b - float(b_global))
            # step
            w -= lr * grad_w
            b -= lr * grad_b
        return w, float(b)

    async def on_start(self):
        try:
            host, port = self.system.host, self.system.port
            self.system.tell(self.scheduler, RegisterWorker(self.name, host, port))
        except Exception:
            pass
        self.system.tell(self.scheduler, GiveMeWork(self.name))

    async def default_behavior(self, message):
        if isinstance(message, HealthPing):
            self.system.tell(message.monitor_name, HealthAck(self.name))
            return
        if isinstance(message, CrashMe):
            raise Exception("Simulated crash")

        if isinstance(message, SetGlobalModel):
            self.global_coef = np.array(message.coef, dtype=float).reshape(1, -1)
            self.global_intercept = float(message.intercept)
            return
        if isinstance(message, AssignTeam):
            team = message.team_name
            print(f"[{self.name}] dobio posao: {team}")

            # lokalno izdvajanje podataka za tim
            if self.train_df is None:
                print(f"[{self.name}] nema lokalni train_df, ne mogu da izdvojim podatke za {team}")
                # završi posao bez rezultata
                self.system.tell(self.scheduler, WorkDone(self.name))
                self.system.tell(self.scheduler, GiveMeWork(self.name))
                return
            data = self.train_df[(self.train_df["home_team"] == team) | (self.train_df["away_team"] == team)]

            # pripremi podatke
            X = self.imputer.transform(data[self.features])
            y = data["home_win"]

            # PROVERA: da li y ima bar dve klase
            if len(set(y)) < 2:
                print(f"[{self.name}] tim {team} nema dovoljno klasa, preskačem.")
                # označi posao završenim i traži novi
                self.system.tell(self.scheduler, WorkDone(self.name))
                self.system.tell(self.scheduler, GiveMeWork(self.name))
                return

            # treniraj model
            if self.fedprox_mu > 0.0 and self.global_coef is not None and self.global_intercept is not None:
                # Pravi FedProx sa proksimalnim terminom oko globalnih težina
                try:
                    w, b = self._train_fedprox(
                        X, y,
                        mu=self.fedprox_mu,
                        w_global=self.global_coef.ravel(),
                        b_global=float(self.global_intercept),
                        epochs=120,
                        lr=0.1,
                        l2=0.0,
                    )
                    coef_out, intercept_out = w, b
                except Exception as e:
                    print(f"[{self.name}] FedProx fallback zbog greške: {e}")
                    model = LogisticRegression(max_iter=500)
                    model.fit(X, y)
                    coef_out, intercept_out = model.coef_[0], float(model.intercept_[0])
            else:
                model = LogisticRegression(max_iter=500)
                model.fit(X, y)
                coef_out, intercept_out = model.coef_[0], float(model.intercept_[0])

            share = ModelShare(team, coef_out, intercept_out)

            self.system.tell("aggregator_p2p", share)

            self.system.tell(self.scheduler, WorkDone(self.name))
            self.system.tell(self.scheduler, GiveMeWork(self.name))

        elif isinstance(message, NoMoreWork):
            print(f"[{self.name}] nema više posla, završavam.")
