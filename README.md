# NBA Champion 2026 – Federativno Učenje & Aktorski Sistem

Distribuirani sistem za predikciju šampiona (i plej-of serija) koristeći federativno učenje (FedAvg / FedProx) na istorijskim NBA podacima. Implementirano preko prilagođenog asinhronog aktorskog modela sa više režima rada: provider (centralni agregator), P2P i gossip. Uključen je i gRPC transport, CRDT replikacija i simulacija plej-of bracket-a (QF→SF→F) sa upisom u SQLite.

## Sadržaj

1. Karakteristike
2. Arhitektura (aktori & poruke)
3. Zahtevi i instalacija
4. Priprema okruženja i podataka
5. Pokretanje (provider / p2p / p2p-gossip)
6. gRPC transport (stubovi, pokretanje, troubleshooting)
7. Federativni algoritmi (FedAvg, FedProx)
8. Playoff simulacija i čitanje šampiona
9. CRDT (PN-Counter, LWW-Map)
10. Benchmarking
11. Testiranje (pytest)
12. Struktura SQLite baze
13. Česta pitanja & problemi
14. Sledeći koraci / ideje

## 1. Karakteristike

- Federativno učenje: FedAvg + opcioni FedProx (μ regularizacija na klijentu i serverski blend).
- Više režima orkestracije: centralni provider, P2P work-stealing, gossip sinhronizacija rundi (barijera PeerReady).
- Evaluator: accuracy, log_loss, brier; baseline centralizovani model; simulacija plej-of serija & bracket.
- CRDT: PN-Counter (broj rundi) i LWW-Map (primer replikacije) sa replikatorom.
- Health & Supervizor: ping/ack, restart logika.
- gRPC transport (pored TCP) sa generisanim protobuf stubovima.
- SQLite perzistencija: rezultati po rundi + playoff serije (sa stage: QF/SF/F) + globalni model JSON.
- Skripte: benchmark, playoff champion ekstrakcija.

## 2. Arhitektura

Aktori (asyncio):

- `TeamNodeWorker` / `TeamNodeP2P`: lokalni trening, slanje modelskih ažuriranja.
- `Aggregator` (provider) i `AggregatorP2P`: prikupljanje & FedAvg / slanje globalnog modela.
- `Evaluator`: računa metrike & simulira playoff bracket.
- `Scheduler` (u provider modu): dodela timova / radnih komada (work stealing).
- `CrdtReplicator`: širenje CRDT delti.
- `HealthMonitor` & `Supervisor`: nadzor i restart.

Glavne poruke (nije iscrpno): `TrainRequest`, `ModelShare` / `RoundComplete`, `SetGlobalModel`, `EvalRequest` / `EvalReport`, `PeerList`, `PeerReady`, `StartRound`, `CrdtDelta`, `HealthPing/Ack`.

## 3. Zahtevi i instalacija

Preporuka: Python 3.11+ (ok i 3.13), PowerShell na Windows-u.

Kreiraj i aktiviraj venv:

powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip setuptools wheel

# scikit-learn numpy pandas grpcio grpcio-tools

Generisanje gRPC stubova:

powershell
python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto

## 4. Priprema podataka

Direktorijum `dataset/` već sadrži očišćen CSV (`nba_games_clean.csv`) i timske CSV fajlove u `dataset/teams` ili `teams/`. Trenutni kod koristi podatke direktno – nije potreban dodatni import. Ako dodaš nove CSV fajlove pazi da kolone budu konzistentne.

## 5. Pokretanje režima

Ulazna tačka: `main.py` sa argumentom `--mode`.

Zajednički parametri (primeri):

- `--mode` provider | p2p | p2p-gossip
- `--node` identifikator čvora (npr. MIA)
- `--host` / `--port` mrežni parametri
- `--transport` tcp | grpc
- `--rounds` broj federativnih rundi (provider/p2p)
- `--fedprox-mu` koeficijent μ (opciono)

### 5.1 Provider mod

Pokreće centralni scheduler + aggregator + evaluator.

powershell
python main.py --mode provider --node HUB --host 127.0.0.1 --port 5000 --rounds 2

Radnici (autoregistracija / work stealing):

powershell
python main.py --mode provider --node W1 --host 127.0.0.1 --port 5001 --peers HUB@127.0.0.1:5000
python main.py --mode provider --node W2 --host 127.0.0.1 --port 5002 --peers HUB@127.0.0.1:5000

### 5.2 P2P mod

Svaki čvor radi lokalni trening i šalje model aggregatoru-ravnopravno.

powershell
python main.py --mode p2p --node MIA --host 127.0.0.1 --port 5100 --rounds 2
python main.py --mode p2p --node BOS --host 127.0.0.1 --port 5101 --peers MIA@127.0.0.1:5100 --rounds 2
python main.py --mode p2p --node CHI --host 127.0.0.1 --port 5102 --peers MIA@127.0.0.1:5100,BOS@127.0.0.1:5101 --rounds 2

### 5.3 Gossip mod

Reporter (barijera + globalni model), ostali peer-ovi šalju samo lokalni share.

powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5200 --peers BOS@127.0.0.1:5201,CHI@127.0.0.1:5202 --reporter --gossip-rounds 2 --gossip-eval
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5201 --peers MIA@127.0.0.1:5200,CHI@127.0.0.1:5202
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5202 --peers MIA@127.0.0.1:5200,BOS@127.0.0.1:5201

### 5.4 gRPC transport

Dodaj `--transport grpc` na sve procese (posle generisanja stubova). Portovi ostaju isti.

powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5300 --peers BOS@127.0.0.1:5301,CHI@127.0.0.1:5302 --reporter --gossip-rounds 2 --gossip-eval --transport grpc

python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5301 --peers
MIA@127.0.0.1:5300,CHI@127.0.0.1:5302 --transport grpc

python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5302 --peers MIA@127.0.0.1:5300,BOS@127.0.0.1:5301 --transport grpc

Ako dobiješ poruku o stubovima – prati instrukcije iz greške.

## 6. FedProx

Aktiviraj dodavanjem `--fedprox-mu` (npr. 0.01). Radnici u klijent treninzima dodaju proximal regularizaciju prema globalnom modelu.

powershell
python main.py --mode p2p --node MIA --host 127.0.0.1 --port 5400 --rounds 3 --fedprox-mu 0.01

## 7. Playoff simulacija & šampion

- Evaluator posle poslednje runde (ili kada je aktivirano `--gossip-eval`) simulira bracket: QF (do 16 timova) → SF → F.
- Serije se upisuju u tabelu `playoffs` sa kolonom `stage` (QF/SF/F).
- Skripta za čitanje finala:

powershell
python scripts/who_wins_playoffs.py

Primer izlaza:

Finals: LBN vs UTH -> 4:3 winner=LBN ...
Predicted champion: LBN

## 8. CRDT

- PN-Counter: inkrement po rundi, repliciran preko replikatora.
- LWW-Map: čuvanje ključ→vrednost sa timestamp orderingom.
  Logovi označeni sa `[CRDT]` prikazuju vrednosti.

## 9. Benchmarking

`bench.py` pokreće scenarije i meri vreme do pojave reda u `results` tabeli.

powershell
python bench.py

Rezultat: `storage/bench.json`.

## 10. Testovi

Pokretanje:

powershell
pytest -q

Postoje osnovni testovi za serializaciju poruka i FedAvg agregaciju.

## 11. SQLite struktura

Fajl: `storage/results.db`

- `results(id, timestamp, round_idx, acc, log_loss, brier, model_json)`
- `playoffs(id, round_idx, team_a, team_b, best_of, wins_a, wins_b, winner, stage, p_a_win, ts)`
  Globalni model (sklearn koeficijenti) dodatno se čuva u `global_model.json`.

## 12. Troubleshooting

| Problem                   | Uzrok                                              | Rešenje                                                |
| ------------------------- | -------------------------------------------------- | ------------------------------------------------------ |
| gRPC: "stubs not found"   | Nema generisanih `actor_pb2*.py`                   | Pokreni protoc komandu iz sekcije 3                    |
| Import greška `actor_pb2` | Nema `rpc/__init__.py` ili nije paket              | Proveri da postoji datoteka `rpc/__init__.py`          |
| Nema novih playoff redova | Nije poslednja runda / `--gossip-eval` izostavljen | Pokreni sa `--gossip-eval` ili sačekaj poslednju rundu |
| FedProx nema efekat       | μ=0 ili nema globalnog modela prve runde           | Povećaj `--fedprox-mu` (>0), više rundi                |
| Port zauzet               | Prethodni proces nije ugašen                       | Prekini python proces (Task Manager) i promeni port    |

## 13. Sledeći koraci (ideje)

- TLS za gRPC (sertifikati, secure_channel)
- WebSocket transport
- PyTorch model varijanta
- ScheduleWatcher ingest realnog rasporeda (nba_api)
- DuckDB / journaling za poruke
- Napredni membership (NodeJoin/Leave full lifecycle)
- CI workflow i veća pokrivenost testovima

---

pokreni gossip demo (TCP):

powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5200 --peers BOS@127.0.0.1:5201,CHI@127.0.0.1:5202 --reporter --gossip-rounds 2 --gossip-eval
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5201 --peers MIA@127.0.0.1:5200,CHI@127.0.0.1:5202
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5202 --peers MIA@127.0.0.1:5200,BOS@127.0.0.1:5201

Zatim proveri šampiona:

powershell
python scripts/who_wins_playoffs.py
