import json
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans


def compute_team_clusters(
    train_df: pd.DataFrame,
    features: list[str],
    n_clusters: int = 4,
    random_state: int = 42,
    persist_path: str = "storage/clusters.json",
) -> Dict[str, int]:
    Path("storage").mkdir(exist_ok=True)

    teams = sorted(set(train_df["home_team"]).union(set(train_df["away_team"])) )

    vecs = []
    names = []
    for t in teams:
        home = train_df[train_df["home_team"] == t]
        away = train_df[train_df["away_team"] == t]
        # Build vector in the same order as training features
        # features are: [ft_pct_home, fg_pct_home, ft_pct_away, fg_pct_away]
        def _mean_safe(series):
            try:
                return float(np.nanmean(series))
            except Exception:
                return float("nan")
        v = [
            _mean_safe(home["ft_pct_home"]) if len(home) else 0.5,
            _mean_safe(home["fg_pct_home"]) if len(home) else 0.5,
            _mean_safe(away["ft_pct_away"]) if len(away) else 0.5,
            _mean_safe(away["fg_pct_away"]) if len(away) else 0.5,
        ]
        names.append(t)
        vecs.append(v)

    X = np.array(vecs, dtype=float)
    # Replace any remaining NaNs with column means
    col_means = np.nanmean(X, axis=0)
    inds = np.where(np.isnan(X))
    X[inds] = np.take(col_means, inds[1])

    kmeans = KMeans(n_clusters=max(1, int(n_clusters)), n_init=10, random_state=random_state)
    labels = kmeans.fit_predict(X)

    mapping = {name: int(lbl) for name, lbl in zip(names, labels)}

    with open(persist_path, "w", encoding="utf-8") as f:
        json.dump({"clusters": mapping, "n_clusters": int(n_clusters)}, f, indent=2)

    return mapping
