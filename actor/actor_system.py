import asyncio
import json


class Actor:
    def __init__(self, name, system):
        self.name = name
        self.system = system
        self.mailbox = asyncio.Queue()
        self.behavior = self.default_behavior
        self.alive = True

    async def on_start(self):
        pass

    async def on_stop(self):
        pass

    def become(self, behavior):
        self.behavior = behavior

    async def default_behavior(self, message):
        print(f"[{self.name}] received: {message}")

    async def run(self):
        await self.on_start()
        while self.alive:
            message = await self.mailbox.get()
            if message == "__STOP__":
                self.alive = False
                break
            await self.behavior(message)
        await self.on_stop()


class ActorSystem:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, transport: str = "tcp"):
        self.actors = {}
        self.host = host
        self.port = port
        self.transport = transport  # "tcp" or "grpc"
        self._server = None
        self._grpc_server = None
        self._peers = {}  # logical actor name -> (host, port, transport)

    async def start_network(self):
        if self.transport == "grpc":
            try:
                from rpc.grpc_transport import GrpcServer
            except Exception as e:
                raise RuntimeError("gRPC transport selected, but rpc/grpc_transport.py or dependencies are missing. Install grpcio and generate stubs from proto/actor.proto") from e
            self._grpc_server = GrpcServer(self.host, self.port, self._handle_envelope)
            self.host, self.port = await self._grpc_server.start()
            print(f"[ActorSystem] listening (gRPC) on {self.host}:{self.port}")
        else:
            self._server = await asyncio.start_server(self._handle_conn, self.host, self.port)
            sock = self._server.sockets[0].getsockname()
            self.host, self.port = sock[0], sock[1]
            print(f"[ActorSystem] listening on {self.host}:{self.port}")

    def register_peer(self, name: str, host: str, port: int, transport: str | None = None):
        tx = transport or self.transport
        self._peers[name] = (host, int(port), tx)

    async def _handle_conn(self, reader, writer):
        try:
            data = await reader.readline()
            if not data:
                return
            msg = json.loads(data.decode("utf-8"))
            await self._handle_envelope(msg)
        except Exception as e:
            print("[ActorSystem] recv error:", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_envelope(self, msg: dict):
        target = msg.get("target")
        mtype = msg.get("type")
        payload = msg.get("payload", {})

        # Deserialize known message types and forward locally
        if mtype == "ModelShare":
            from actor.p2p import ModelShare
            import numpy as np
            m = ModelShare(
                payload["sender"],
                np.array(payload["coef"], dtype=float),
                float(payload["intercept"]),
                payload.get("version"),
                payload.get("ts_ms"),
            )
            self.tell(target, m)
        elif mtype == "TrainRequest":
            from actor.aggregator import TrainRequest
            self.tell(target, TrainRequest())
        elif mtype == "StartRound":
            from actor.p2p import StartRound
            self.tell(target, StartRound())
        elif mtype == "GiveMeWork":
            from actor.scheduler import GiveMeWork
            self.tell(target, GiveMeWork(payload["worker"]))
        elif mtype == "AssignTeam":
            from actor.scheduler import AssignTeam
            self.tell(target, AssignTeam(payload["team_name"]))
        elif mtype == "NoMoreWork":
            from actor.scheduler import NoMoreWork
            self.tell(target, NoMoreWork())
        elif mtype == "RegisterWorker":
            from actor.scheduler import RegisterWorker
            self.tell(target, RegisterWorker(payload["worker"], payload["host"], int(payload["port"])) )
        elif mtype == "WorkDone":
            from actor.scheduler import WorkDone
            self.tell(target, WorkDone(payload["worker"]))
        elif mtype == "AllDone":
            from actor.aggregator import AllDone
            self.tell(target, AllDone())
        elif mtype == "RoundComplete":
            from actor.aggregator import RoundComplete
            self.tell(target, RoundComplete(int(payload["round_idx"]), int(payload["total_rounds"]), float(payload.get("fedprox_mu", 0.0))))
        elif mtype == "SetGlobalModel":
            from actor.aggregator import SetGlobalModel
            import numpy as np
            coef = np.array(payload["coef"], dtype=float)
            intercept = float(payload["intercept"])
            self.tell(target, SetGlobalModel(coef, intercept))
        elif mtype == "SetClusterModels":
            from actor.aggregator import SetClusterModels
            # payload: { cid: {"coef": [...], "intercept": float} }
            import numpy as np
            cluster_models = {}
            for cid, m in (payload or {}).items():
                cluster_models[cid] = {"coef": np.array(m["coef"], dtype=float).reshape(1, -1), "intercept": float(m["intercept"]) }
            self.tell(target, SetClusterModels(cluster_models))
        elif mtype == "SetTeamClusters":
            from actor.aggregator import SetTeamClusters as AggSetTeamClusters
            from actor.scheduler import SetTeamClusters as SchSetTeamClusters
            mapping = dict(payload.get("mapping", {}))
            # Forward to either actor type
            if target in self.actors and isinstance(self.actors[target], object):
                try:
                    self.tell(target, AggSetTeamClusters(mapping))
                except Exception:
                    self.tell(target, SchSetTeamClusters(mapping))
        elif mtype == "PeerList":
            from actor.p2p import PeerList
            self.tell(target, PeerList(payload["peers"], bool(payload.get("is_reporter", False)), payload.get("reporter_name"), int(payload.get("total_rounds", 1))))
        elif mtype == "PeerReady":
            from actor.p2p import PeerReady
            self.tell(target, PeerReady(payload["peer_name"]))
        elif mtype == "EvalRequest":
            from actor.evaluator import EvalRequest
            self.tell(target, EvalRequest(payload.get("pairs"), int(payload.get("best_of", 7)), payload.get("reply_to"), payload.get("round_idx")))
        elif mtype == "EvalReport":
            from actor.evaluator import EvalReport
            self.tell(target, EvalReport(payload.get("results")))
        elif mtype == "LwwPut":
            from actor.crdt import LwwPut
            self.tell(target, LwwPut(payload["key"], payload["value"], int(payload.get("ts")) if payload.get("ts") is not None else None))
        elif mtype == "LwwGet":
            from actor.crdt import LwwGet
            self.tell(target, LwwGet(payload["key"]))
        elif mtype == "LwwDump":
            from actor.crdt import LwwDump
            self.tell(target, LwwDump())
        elif mtype == "CrdtMerge":
            from actor.crdt import CrdtMerge
            self.tell(target, CrdtMerge(payload["delta"]))
        elif mtype == "Replicate":
            from actor.crdt import Replicate
            self.tell(target, Replicate(payload["delta"]))
        elif mtype == "Attach":
            from actor.crdt import Attach
            self.tell(target, Attach(payload["map_actor_name"]))
        elif mtype == "AddPeer":
            from actor.crdt import AddPeer
            self.tell(target, AddPeer(payload["remote_actor_name"], payload["host"], int(payload["port"])) )
        elif mtype == "HealthPing":
            from actor.health import HealthPing
            self.tell(target, HealthPing(payload["monitor_name"]))
        elif mtype == "HealthAck":
            from actor.health import HealthAck
            self.tell(target, HealthAck(payload["actor_name"]))
        elif mtype == "CrashMe":
            from actor.health import CrashMe
            self.tell(target, CrashMe())
        else:
            print(f"[ActorSystem] Unknown message type: {mtype}")

    async def _send_remote(self, host: str, port: int, envelope: dict, transport: str = "tcp"):
        if transport == "grpc":
            try:
                from rpc.grpc_transport import send_envelope
                await send_envelope(host, port, envelope)
                return
            except Exception as e:
                print(f"[ActorSystem] gRPC send {host}:{port} failed:", e)
                return
        # default tcp
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.write((json.dumps(envelope) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            print(f"[ActorSystem] send {host}:{port} failed:", e)

    def create_actor(self, name, actor_factory):
        actor = actor_factory(name, self)
        self.actors[name] = actor
        asyncio.create_task(actor.run())
        return actor

    def tell(self, actor_name: str, message):
        # Local actor
        if actor_name in self.actors:
            self.actors[actor_name].mailbox.put_nowait(message)
            return
        # Remote actor by logical name
        if actor_name in self._peers:
            host, port, tx = self._peers[actor_name]
            envelope = self._serialize(actor_name, message)
            asyncio.create_task(self._send_remote(host, port, envelope, tx))
        else:
            print(f"Actor {actor_name} does not exist or is not registered")

    def _serialize(self, target: str, message):
        mname = message.__class__.__name__
        if mname == "ModelShare":
            payload = {
                "sender": message.sender,
                "coef": list(message.coef),
                "intercept": float(message.intercept),
            }
            if getattr(message, "version", None) is not None:
                payload["version"] = int(message.version)
            import time
            payload["ts_ms"] = getattr(message, "ts_ms", None)
            if payload["ts_ms"] is None:
                try:
                    payload["ts_ms"] = int(time.time() * 1000)
                except Exception:
                    payload["ts_ms"] = 0
            return {"target": target, "type": "ModelShare", "payload": payload}
        if mname == "TrainRequest":
            return {"target": target, "type": "TrainRequest"}
        if mname == "StartRound":
            return {"target": target, "type": "StartRound"}
        if mname == "GiveMeWork":
            return {"target": target, "type": "GiveMeWork", "payload": {"worker": message.worker}}
        if mname == "AssignTeam":
            return {"target": target, "type": "AssignTeam", "payload": {"team_name": message.team_name}}
        if mname == "NoMoreWork":
            return {"target": target, "type": "NoMoreWork"}
        if mname == "RegisterWorker":
            return {"target": target, "type": "RegisterWorker", "payload": {"worker": message.worker, "host": message.host, "port": message.port}}
        if mname == "WorkDone":
            return {"target": target, "type": "WorkDone", "payload": {"worker": message.worker}}
        if mname == "AllDone":
            return {"target": target, "type": "AllDone"}
        if mname == "RoundComplete":
            return {"target": target, "type": "RoundComplete", "payload": {"round_idx": message.round_idx, "total_rounds": message.total_rounds, "fedprox_mu": message.fedprox_mu}}
        if mname == "SetGlobalModel":
            return {"target": target, "type": "SetGlobalModel", "payload": {"coef": list(message.coef.ravel()), "intercept": float(message.intercept)}}
        if mname == "SetClusterModels":
            payload = {cid: {"coef": list(m["coef"].ravel()), "intercept": float(m["intercept"]) } for cid, m in message.cluster_models.items()}
            return {"target": target, "type": "SetClusterModels", "payload": payload}
        if mname == "SetTeamClusters":
            return {"target": target, "type": "SetTeamClusters", "payload": {"mapping": message.mapping}}
        if mname == "PeerList":
            return {"target": target, "type": "PeerList", "payload": {"peers": list(message.peers), "is_reporter": bool(message.is_reporter), "reporter_name": message.reporter_name, "total_rounds": message.total_rounds}}
        if mname == "PeerReady":
            return {"target": target, "type": "PeerReady", "payload": {"peer_name": message.peer_name}}
        if mname == "EvalRequest":
            return {"target": target, "type": "EvalRequest", "payload": {"pairs": message.pairs, "best_of": message.best_of, "reply_to": message.reply_to, "round_idx": message.round_idx}}
        if mname == "EvalReport":
            return {"target": target, "type": "EvalReport", "payload": {"results": message.results}}
        if mname == "LwwPut":
            return {"target": target, "type": "LwwPut", "payload": {"key": message.key, "value": message.value, "ts": message.ts}}
        if mname == "LwwGet":
            return {"target": target, "type": "LwwGet", "payload": {"key": message.key}}
        if mname == "LwwDump":
            return {"target": target, "type": "LwwDump"}
        if mname == "CrdtMerge":
            return {"target": target, "type": "CrdtMerge", "payload": {"delta": message.delta}}
        if mname == "Replicate":
            return {"target": target, "type": "Replicate", "payload": {"delta": message.delta}}
        if mname == "Attach":
            return {"target": target, "type": "Attach", "payload": {"map_actor_name": message.map_actor_name}}
        if mname == "AddPeer":
            return {"target": target, "type": "AddPeer", "payload": {"remote_actor_name": message.remote_actor_name, "host": message.host, "port": message.port}}
        if mname == "HealthPing":
            return {"target": target, "type": "HealthPing", "payload": {"monitor_name": message.monitor_name}}
        if mname == "HealthAck":
            return {"target": target, "type": "HealthAck", "payload": {"actor_name": message.actor_name}}
        if mname == "CrashMe":
            return {"target": target, "type": "CrashMe"}
        # Fallback to class name only
        return {"target": target, "type": mname}

    def stop_actor(self, actor_name: str):
        if actor_name in self.actors:
            self.tell(actor_name, "__STOP__")
