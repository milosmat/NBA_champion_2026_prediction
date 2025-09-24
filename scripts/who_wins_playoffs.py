import sqlite3
import json
from pathlib import Path

DB = Path('storage/results.db')
if not DB.exists():
    print('No storage/results.db found')
    raise SystemExit(1)

con = sqlite3.connect(DB)
cur = con.cursor()

cur.execute("SELECT COALESCE(MAX(round_idx), -1) FROM playoffs")
row = cur.fetchone()
latest_round = row[0] if row else None

if latest_round is None:
    print('No playoffs rows found')
    raise SystemExit(0)

print(f'Latest playoffs round_idx = {latest_round}')

cur.execute(
    """
    SELECT team_a, team_b, best_of, wins_a, wins_b, winner, p_a_win, ts
    FROM playoffs
    WHERE round_idx IS ? AND stage = 'F'
    ORDER BY id DESC
    """,
    (latest_round,)
)
finals = cur.fetchall()
if finals:
    a,b,bo,wa,wb,winner,p,ts = finals[0]
    print(f'Finals: {a} vs {b} -> {wa}:{wb} winner={winner} (best-of-{bo}, p_a_win={p:.3f}) at {ts}')
    print(f'Predicted champion: {winner}')
    con.close()
    raise SystemExit(0)

cur.execute(
    """
    SELECT team_a, team_b, best_of, wins_a, wins_b, winner, p_a_win, ts
    FROM playoffs
    WHERE round_idx IS ?
    ORDER BY id ASC
    """,
    (latest_round,)
)
series = cur.fetchall()

if not series:
    print('No series found for latest round')
    raise SystemExit(0)

print('Series results:')
for (a,b,bo,wa,wb,winner,p,ts) in series:
    print(f'  {a} vs {b} (best-of-{bo}) -> {wa}:{wb} winner={winner} (p_a_win={p:.3f}) at {ts}')

if len(series) == 1:
    champ = series[0][5]
    print(f'Predicted champion (final series winner): {champ}')
else:
    winners = [r[5] for r in series]
    print('Multiple series found; cannot infer a single champion without bracket stages.')
    print('Winners per series:', winners)

con.close()
