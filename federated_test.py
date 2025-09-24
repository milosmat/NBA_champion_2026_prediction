import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.impute import SimpleImputer

df = pd.read_csv("dataset/nba_games_clean.csv")

last_season = df["season"].max()
print("Poslednja sezona u podacima:", last_season)

train = df[df["season"] < last_season]
test = df[df["season"] == last_season]


features = [
    "ft_pct_home", "fg_pct_home",
    "ft_pct_away", "fg_pct_away"
]

imputer = SimpleImputer(strategy="mean")
imputer.fit(train[features])

X_test = test[features]
y_test = test["home_win"]

weights = []
intercepts = []

teams = pd.unique(train[["home_team", "away_team"]].values.ravel("K"))
for team in teams:
    team_data = train[(train["home_team"] == team) | (train["away_team"] == team)]
    if len(team_data) < 50:
        continue

    X_train = imputer.transform(team_data[features])
    y_train = team_data["home_win"]

    model = LogisticRegression(max_iter=500)
    model.fit(X_train, y_train)

    weights.append(model.coef_[0])
    intercepts.append(model.intercept_[0])

global_weights = np.mean(weights, axis=0).reshape(1, -1)
global_intercept = np.mean(intercepts, axis=0).reshape(1,)

global_model = LogisticRegression(max_iter=500)
global_model.coef_ = global_weights
global_model.intercept_ = global_intercept
global_model.classes_ = np.array([0, 1])

X_test_imputed = imputer.transform(X_test)
y_pred = global_model.predict(X_test_imputed)
acc_fedavg = accuracy_score(y_test, y_pred)

print(f"Globalni model FedAvg tačnost na poslednjoj sezoni: {acc_fedavg:.3f}")

X_train_all = imputer.transform(train[features])
y_train_all = train["home_win"]

baseline_model = LogisticRegression(max_iter=500)
baseline_model.fit(X_train_all, y_train_all)

y_pred_baseline = baseline_model.predict(X_test_imputed)
acc_baseline = accuracy_score(y_test, y_pred_baseline)

print(f"Centralizovani model tačnost na poslednjoj sezoni: {acc_baseline:.3f}")

print("\n===== Poređenje =====")
print(f"Federativni (FedAvg): {acc_fedavg:.3f}")
print(f"Centralizovani:       {acc_baseline:.3f}")
