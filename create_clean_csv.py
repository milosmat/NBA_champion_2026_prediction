import pandas as pd

df = pd.read_csv("dataset/csv/game.csv")

df = df[[
    "season_id", "game_date",
    "team_abbreviation_home", "team_abbreviation_away",
    "pts_home", "pts_away",
    "fg_pct_home", "fg3_pct_home", "ft_pct_home",
    "reb_home", "ast_home", "stl_home", "blk_home", "tov_home", "pf_home",
    "fg_pct_away", "fg3_pct_away", "ft_pct_away",
    "reb_away", "ast_away", "stl_away", "blk_away", "tov_away", "pf_away"
]]

df["home_win"] = (df["pts_home"] > df["pts_away"]).astype(int)

df = df.rename(columns={
    "season_id": "season",
    "game_date": "date",
    "team_abbreviation_home": "home_team",
    "team_abbreviation_away": "away_team",
    "pts_home": "home_points",
    "pts_away": "away_points"
})

print(df.head())

df.to_csv("dataset/nba_games_clean.csv", index=False)
print("Prošireni clean dataset sačuvan u dataset/nba_games_clean.csv")
