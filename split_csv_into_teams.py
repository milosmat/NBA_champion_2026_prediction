import pandas as pd
import os

df = pd.read_csv("dataset/nba_games_clean.csv")

output_dir = "dataset/teams"
os.makedirs(output_dir, exist_ok=True)

teams = pd.unique(df[["home_team", "away_team"]].values.ravel("K"))

for team in teams:
    team_games = df[(df["home_team"] == team) | (df["away_team"] == team)]
    team_games.to_csv(f"{output_dir}/data_{team}.csv", index=False)

print(f"Napravljeno {len(teams)} fajlova u folderu {output_dir}")
