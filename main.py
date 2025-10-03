import asyncio
import argparse
import pandas as pd
from sklearn.impute import SimpleImputer
from actor.actor_system import ActorSystem
from actor.aggregator import Aggregator, TrainRequest, AggregatorP2P, TeamNode
from actor.p2p import TeamNodeP2P, PeerList, StartRound
from actor.evaluator import Evaluator
from actor.supervisor import Supervisor
from actor.health import HealthMonitor
from actor.crdt import PN_Counter, GetValue, LWW_Map, CrdtReplicator, Attach, AddPeer, LwwPut, LwwDump
from actor.scheduler import Scheduler
from actor.worker import TeamNodeWorker
from actor.worker import TeamNodeWorker as _W
import argparse
from clustering import compute_team_clusters


def parse_args():
    p = argparse.ArgumentParser(description="Pokretanje nodova")
    p.add_argument("--mode", choices=["provider", "p2p", "p2p-gossip"], default="p2p")
    p.add_argument("--node", required=True, help="Ime noda (MIA, BOS...)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--peers", default="", help="Lista: IME@HOST:PORT,IME2@HOST:PORT")
    p.add_argument("--workers", type=int, default=2, help="Broj worker aktora po nodu")
    p.add_argument("--rounds", type=int, default=1, help="Broj rundi u P2P režimu")
    p.add_argument("--fedprox_mu", type=float, default=0.0, help="Proksimalni koeficijent (0=FedAvg)")
    p.add_argument("--reporter", action="store_true", help="Samo za p2p-gossip: ovaj nod šalje GlobalModel evaluatoru")
    p.add_argument("--gossip-rounds", type=int, default=1, help="Broj rundi u p2p-gossip modu")
    p.add_argument("--gossip-eval", action="store_true", help="Posle poslednje gossip runde traži playoff evaluaciju")
    p.add_argument("--transport", choices=["tcp", "grpc"], default="tcp", help="Transport sloj: tcp (default) ili grpc (opciono)")
    p.add_argument("--n-clusters", type=int, default=4, help="Broj ML klastera timova (KMeans) u P2P režimu")
    return p.parse_args()


async def main():
    args = parse_args()
    mode = args.mode
    node_name = args.node
    host, port = args.host, args.port
    raw_peers = [p.strip() for p in args.peers.split(",") if p.strip()]

    peers = []
    for raw in raw_peers:
        try:
            name_part, addr_part = raw.split("@", 1)
            host_part, port_part = addr_part.split(":", 1)
            peers.append((name_part, host_part, int(port_part)))
        except ValueError:
            print(f"[WARN] Neispravan peer format: {raw}, koristi IME@HOST:PORT")

    df = pd.read_csv("dataset/nba_games_clean.csv")
    last_season = df["season"].max()
    train = df[df["season"] < last_season]
    test = df[df["season"] == last_season]

    features = ["ft_pct_home", "fg_pct_home", "ft_pct_away", "fg_pct_away"]
    imputer = SimpleImputer(strategy="mean")
    imputer.fit(train[features])

    system = ActorSystem(host=host, port=port, transport=args.transport)
    await system.start_network()

    for (pname, phost, pport) in peers:
        system.register_peer(pname, phost, pport)

    if mode == "provider":
        supervisor = system.create_actor("supervisor", lambda name, sys: Supervisor(name, sys))
        if node_name == "LAL":
            system.create_actor("crdt", lambda n, s: PN_Counter(n, s))
            system.create_actor("evaluator", lambda n, s: Evaluator(n, s, features, imputer, test, train_data=train, persist_path="global_model.json"))
            system.create_actor("aggregator", lambda n, s: Aggregator(n, s, team_count=0))
            sample_teams = sorted(df["home_team"].unique())[:3]
            for t in sample_teams:
                local_df = train[(train["home_team"] == t) | (train["away_team"] == t)]
                system.create_actor(f"team_{t}", lambda n, s, data=local_df: TeamNode(n, s, data, features, imputer))
            system.tell("aggregator", TrainRequest())
            system.tell("crdt", GetValue())
        return

    # === P2P-GOSSIP mini demo (svaki nod trenira lokalno i deli težine) ===
    if mode == "p2p-gossip":
        for (pname, phost, pport) in peers:
            system.register_peer(f"p2p_{pname}", phost, pport)

        if args.reporter:
            system.create_actor("crdt", lambda n, s: PN_Counter(n, s))
            system.create_actor(
                "evaluator",
                lambda n, s: Evaluator(n, s, features, imputer, test, train_data=train, persist_path="global_model.json"),
            )

        gname = f"p2p_{node_name}"
        system.create_actor(gname, lambda n, s: TeamNodeP2P(n, s, train, features, imputer, total_rounds=int(args.gossip_rounds), eval_after=bool(args.gossip_eval)))

        peer_actor_names = [f"p2p_{pname}" for (pname, _, _) in peers]
        reporter_actor = f"p2p_{node_name}" if args.reporter else (peer_actor_names[0] if peer_actor_names else None)
        system.tell(gname, PeerList(peer_actor_names, is_reporter=bool(args.reporter), reporter_name=reporter_actor, total_rounds=int(args.gossip_rounds)))

        await asyncio.sleep(3600)
        return

    # === P2P režim sa Scheduler/Worker dinamičkom podelom ===
    else:
        system.create_actor("crdt", lambda n, s: PN_Counter(n, s))
        system.create_actor("evaluator", lambda n, s: Evaluator(n, s, features, imputer, test, train_data=train, persist_path="global_model.json"))

        is_reporter = (len(peers) == 0)

        for (pname, phost, pport) in peers:
            system.register_peer(pname, phost, pport)

        if not is_reporter and peers:
            reporter_host, reporter_port = peers[0][1], peers[0][2]
            system.register_peer("scheduler", reporter_host, reporter_port)
            system.register_peer("aggregator_p2p", reporter_host, reporter_port)

        if is_reporter:
            all_teams = sorted(df["home_team"].unique())
            system.create_actor(
                "scheduler",
                lambda n, s: Scheduler(n, s, all_teams, train, features, imputer, rounds=args.rounds, fedprox_mu=args.fedprox_mu)
            )
            system.create_actor("aggregator_p2p", lambda n, s: AggregatorP2P(n, s))
            print("[Main] Scheduler pokrenut na reporter nodu")
            # Compute clusters once on reporter and distribute mapping
            team_clusters = compute_team_clusters(train, features, n_clusters=4)
            try:
                from actor.aggregator import SetTeamClusters as AggSetTeamClusters
                from actor.scheduler import SetTeamClusters as SchSetTeamClusters
                system.tell("aggregator_p2p", AggSetTeamClusters(team_clusters))
                system.tell("scheduler", SchSetTeamClusters(team_clusters))
            except Exception:
                pass

        if not is_reporter:
            worker_names = []
            for i in range(args.workers):
                worker_name = f"worker_{node_name}_{i}"
                mu = float(args.fedprox_mu)
                system.create_actor(
                    worker_name,
                    lambda n, s, train=train, mu=mu: TeamNodeWorker(n, s, features, imputer, "scheduler", train_df=train, fedprox_mu=mu)
                )
                worker_names.append(worker_name)
            system.create_actor("supervisor", lambda n, s: Supervisor(n, s))
            
            for wn in worker_names:
                system.actors["supervisor"].watch(wn, _W, (features, imputer, "scheduler", train, float(args.fedprox_mu)))
            system.create_actor("health", lambda n, s: HealthMonitor(n, s, "supervisor", worker_names, ping_interval=5.0, timeout=10.0))
  
        system.tell("crdt", GetValue())

        lww_name = f"lww_{node_name}"
        repl_name = f"crdt_replicator_{node_name}"
        system.create_actor(repl_name, lambda n, s: CrdtReplicator(n, s))
        system.create_actor(lww_name, lambda n, s, rep=repl_name: LWW_Map(n, s, replicator_name=rep))

        system.tell(repl_name, Attach(lww_name))

        for (pname, phost, pport) in peers:
            system.tell(repl_name, AddPeer(f"lww_{pname}", phost, pport))

        if is_reporter:
            system.tell(lww_name, LwwPut("leader", node_name))
            system.tell(lww_name, LwwDump())
        else:
            system.tell(lww_name, LwwPut("peer", node_name))
            system.tell(lww_name, LwwDump())


    await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
