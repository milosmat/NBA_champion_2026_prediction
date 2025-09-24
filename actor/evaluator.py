from actor.actor_system import Actor
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from actor.aggregator import GlobalModel
import json
from datetime import datetime
import sqlite3

class Evaluator(Actor):
    def __init__(self, name, system, features, imputer, test_data, train_data=None, persist_path: str = "global_model.json"):
        super().__init__(name, system)
        self.features = features
        self.imputer = imputer
        self.test_data = test_data
        self.train_data = train_data
        self.persist_path = persist_path

    async def default_behavior(self, message):
        if isinstance(message, EvalRequest):
            try:
                res = self._simulate_playoffs(best_of=message.best_of, pairs=message.pairs)
                self._persist_playoffs(res, round_idx=message.round_idx)
                if message.reply_to:
                    self.system.tell(message.reply_to, EvalReport(res))
            except Exception as e:
                print("[Evaluator] EvalRequest error:", e)
            return
        if isinstance(message, GlobalModel):
            global_model = LogisticRegression()
            global_model.coef_ = message.coef
            global_model.intercept_ = np.array([message.intercept])
            global_model.classes_ = np.array([0, 1])

            X_test = self.imputer.transform(self.test_data[self.features])
            y_test = self.test_data["home_win"]
            y_pred = global_model.predict(X_test)
            y_prob = getattr(global_model, "predict_proba", None)
            if y_prob is not None:
                prob1 = y_prob(X_test)[:, 1]
                ll = log_loss(y_test, prob1, labels=[0, 1])
                bs = brier_score_loss(y_test, prob1)
            else:
                prob1 = None
                ll = float("nan")
                bs = float("nan")
            acc = accuracy_score(y_test, y_pred)

            baseline_metrics = None
            if self.train_data is not None:
                X_train_all = self.imputer.transform(self.train_data[self.features])
                y_train_all = self.train_data["home_win"]
                base = LogisticRegression(max_iter=500)
                base.fit(X_train_all, y_train_all)
                base_prob = base.predict_proba(X_test)[:, 1]
                base_pred = base.predict(X_test)
                base_acc = accuracy_score(y_test, base_pred)
                base_ll = log_loss(y_test, base_prob, labels=[0, 1])
                base_bs = brier_score_loss(y_test, base_prob)
                baseline_metrics = {"accuracy": base_acc, "log_loss": base_ll, "brier": base_bs}

            print("[Evaluator] Federated (FedAvg) metrics:")
            print(f"  accuracy: {acc:.3f}")
            print(f"  log_loss: {ll:.4f}")
            print(f"  brier:    {bs:.4f}")
            if baseline_metrics:
                print("[Evaluator] Baseline (centralized) metrics:")
                print(f"  accuracy: {baseline_metrics['accuracy']:.3f}")
                print(f"  log_loss: {baseline_metrics['log_loss']:.4f}")
                print(f"  brier:    {baseline_metrics['brier']:.4f}")

            try:
                payload = {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "coef": global_model.coef_.ravel().tolist(),
                    "intercept": float(global_model.intercept_.ravel()[0]),
                    "metrics": {"accuracy": acc, "log_loss": ll, "brier": bs},
                    "baseline": baseline_metrics,
                    "round_idx": getattr(message, "round_idx", None),
                }
                with open(self.persist_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                print(f"[Evaluator] Rezultati i model sačuvani u {self.persist_path}")

                try:
                    conn = sqlite3.connect("storage/results.db")
                    cur = conn.cursor()
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS results (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT NOT NULL,
                            round_idx INTEGER,
                            coef TEXT NOT NULL,
                            intercept REAL NOT NULL,
                            acc REAL,
                            log_loss REAL,
                            brier REAL,
                            base_acc REAL,
                            base_log_loss REAL,
                            base_brier REAL
                        )
                        """
                    )
                    cur.execute(
                        """
                        INSERT INTO results (timestamp, round_idx, coef, intercept, acc, log_loss, brier, base_acc, base_log_loss, base_brier)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            payload["timestamp"],
                            payload["round_idx"],
                            json.dumps(payload["coef"]),
                            payload["intercept"],
                            acc,
                            ll,
                            bs,
                            baseline_metrics.get("accuracy") if baseline_metrics else None,
                            baseline_metrics.get("log_loss") if baseline_metrics else None,
                            baseline_metrics.get("brier") if baseline_metrics else None,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    print("[Evaluator] Rezultati upisani u storage/results.db")
                except Exception as db_e:
                    print(f"[Evaluator] Greška pri upisu u DB: {db_e}")
            except Exception as e:
                print(f"[Evaluator] Greška pri čuvanju modela: {e}")

    async def on_start(self):
        print("[Evaluator] čeka globalni model")

    def _simulate_playoffs(self, best_of: int = 7, pairs: list[tuple[str, str]] | None = None):
        """Simulate a seeded bracket QF -> SF -> F and return list of series dicts with 'stage'.

        - Ratings are derived from per-team home/away probabilities.
        - If 'pairs' is provided, it's treated as initial QF pairs.
        - Returns: list of dicts with keys: a,b,best_of,wins_a,wins_b,winner,p_a_win,stage
        """
        model = LogisticRegression(max_iter=500)
        X_tr = self.imputer.transform(self.train_data[self.features])
        y_tr = self.train_data["home_win"]
        model.fit(X_tr, y_tr)

        teams = sorted(set(self.train_data["home_team"]).union(set(self.train_data["away_team"])) )
        ratings = {}
        for t in teams:
            home = self.train_data[self.train_data["home_team"] == t]
            away = self.train_data[self.train_data["away_team"] == t]
            ph = model.predict_proba(self.imputer.transform(home[self.features]))[:, 1] if len(home) else np.array([0.5])
            pa = model.predict_proba(self.imputer.transform(away[self.features]))[:, 1] if len(away) else np.array([0.5])
            rating = float(np.mean(ph) + (1.0 - np.mean(pa))) / 2.0
            ratings[t] = rating

        if not pairs:
            ordered = sorted(ratings.items(), key=lambda x: x[1], reverse=True)
            seeds = [t for t, _ in ordered[:16]]
            half = len(seeds) // 2
            pairs = [(seeds[i], seeds[-(i+1)]) for i in range(half)]

        def play_series(a: str, b: str, best_of: int):
            need = best_of // 2 + 1
            wins_a = 0
            wins_b = 0
            pa = ratings.get(a, 0.5)
            pb = ratings.get(b, 0.5)
            p_a_win = 0.5 if (pa + pb == 0) else pa / (pa + pb)
            for _ in range(best_of):
                if np.random.rand() < p_a_win:
                    wins_a += 1
                else:
                    wins_b += 1
                if wins_a == need or wins_b == need:
                    break
            winner = a if wins_a > wins_b else b
            return {"a": a, "b": b, "best_of": best_of, "wins_a": wins_a, "wins_b": wins_b, "winner": winner, "p_a_win": p_a_win}

        results = []
        current = list(pairs)
        stage = "QF"
        qf_winners = []
        for (a, b) in current:
            r = play_series(a, b, best_of)
            r["stage"] = stage
            results.append(r)
            qf_winners.append(r["winner"])

        stage = "SF"
        sf_pairs = [(qf_winners[i], qf_winners[i+1]) for i in range(0, len(qf_winners), 2)]
        sf_winners = []
        for (a, b) in sf_pairs:
            r = play_series(a, b, best_of)
            r["stage"] = stage
            results.append(r)
            sf_winners.append(r["winner"])

        stage = "F"
        if len(sf_winners) >= 2:
            final_pair = (sf_winners[0], sf_winners[1])
            r = play_series(final_pair[0], final_pair[1], best_of)
            r["stage"] = stage
            results.append(r)
        elif len(sf_winners) == 1:
            r = {"a": sf_winners[0], "b": sf_winners[0], "best_of": best_of, "wins_a": 0, "wins_b": 0, "winner": sf_winners[0], "p_a_win": 1.0, "stage": stage}
            results.append(r)

        return results

    def _persist_playoffs(self, results, round_idx: int | None = None):
        try:
            conn = sqlite3.connect("storage/results.db")
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS playoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_idx INTEGER,
                    team_a TEXT,
                    team_b TEXT,
                    best_of INTEGER,
                    wins_a INTEGER,
                    wins_b INTEGER,
                    winner TEXT,
                    stage TEXT,
                    p_a_win REAL,
                    ts DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            try:
                cur.execute("SELECT stage FROM playoffs LIMIT 1")
            except Exception:
                cur.execute("ALTER TABLE playoffs ADD COLUMN stage TEXT")
            for r in results:
                cur.execute(
                    "INSERT INTO playoffs(round_idx, team_a, team_b, best_of, wins_a, wins_b, winner, stage, p_a_win) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        int(round_idx) if round_idx is not None else None,
                        r["a"], r["b"], int(r["best_of"]), int(r["wins_a"]), int(r["wins_b"]), r["winner"], r.get("stage"), float(r["p_a_win"]) 
                    ),
                )
            conn.commit()
            conn.close()
            print("[Evaluator] Playoffs upisani u storage/results.db")
        except Exception as e:
            print("[Evaluator] DB error (playoffs):", e)


class EvalRequest:
    def __init__(self, pairs=None, best_of: int = 7, reply_to: str | None = None, round_idx: int | None = None):
        self.pairs = pairs
        self.best_of = int(best_of)
        self.reply_to = reply_to
        self.round_idx = round_idx

class EvalReport:
    def __init__(self, results):
        self.results = results
