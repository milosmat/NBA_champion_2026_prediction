from actor.actor_system import Actor
import time
from typing import Dict, Any

class Increment: pass
class Decrement: pass
class GetValue: pass

class PN_Counter(Actor):
    def __init__(self, name, system):
        super().__init__(name, system)
        self.positive = 0
        self.negative = 0

    async def default_behavior(self, message):
        if isinstance(message, Increment):
            self.positive += 1
            print(f"[CRDT] Increment → vrednost = {self.value()}")
        elif isinstance(message, Decrement):
            self.negative += 1
            print(f"[CRDT] Decrement → vrednost = {self.value()}")
        elif isinstance(message, GetValue):
            print(f"[CRDT] Trenutna vrednost = {self.value()}")

    def value(self):
        return self.positive - self.negative

    async def on_start(self):
        print("[CRDT] PN-Counter pokrenut")


# === LWW-Map (Last-Write-Wins Map) ===
class LwwPut:
    def __init__(self, key: str, value: Any, ts: int | None = None):
        self.key = str(key)
        self.value = value
        self.ts = int(ts) if ts is not None else None

class LwwGet:
    def __init__(self, key: str):
        self.key = str(key)

class LwwDump:
    pass

class CrdtMerge:
    def __init__(self, delta: Dict[str, Dict[str, Any]]):
        self.delta = delta

class Replicate:
    def __init__(self, delta: Dict[str, Dict[str, Any]]):
        self.delta = delta

class Attach:
    def __init__(self, map_actor_name: str):
        self.map_actor_name = map_actor_name

class AddPeer:
    def __init__(self, remote_actor_name: str, host: str, port: int):
        self.remote_actor_name = remote_actor_name
        self.host = host
        self.port = int(port)


class LWW_Map(Actor):
    def __init__(self, name, system, replicator_name: str | None = None):
        super().__init__(name, system)
        self.store: Dict[str, tuple[Any, int]] = {}
        self.replicator_name = replicator_name or "crdt_replicator"

    def _now(self) -> int:
        return time.time_ns()

    def _merge_entry(self, key: str, value: Any, ts: int) -> bool:
        changed = False
        if key not in self.store:
            self.store[key] = (value, ts)
            changed = True
        else:
            _, cur_ts = self.store[key]
            if int(ts) >= int(cur_ts):
                self.store[key] = (value, int(ts))
                changed = True
        return changed

    async def default_behavior(self, message):
        if isinstance(message, LwwPut):
            ts = message.ts if message.ts is not None else self._now()
            if self._merge_entry(message.key, message.value, ts):
                print(f"[LWW] PUT {message.key} = {message.value} @ {ts}")
                delta = {message.key: {"value": message.value, "ts": int(ts)}}
                try:
                    self.system.tell(self.replicator_name, Replicate(delta))
                except Exception:
                    pass
        elif isinstance(message, CrdtMerge):
            applied = 0
            for k, v in message.delta.items():
                if isinstance(v, dict) and "value" in v and "ts" in v:
                    if self._merge_entry(k, v["value"], int(v["ts"])):
                        applied += 1
            print(f"[LWW] MERGE applied {applied} entries, size={len(self.store)}")
        elif isinstance(message, LwwGet):
            val = self.store.get(message.key)
            print(f"[LWW] GET {message.key} -> {val}")
        elif isinstance(message, LwwDump):
            print(f"[LWW] DUMP size={len(self.store)}: {self.store}")

    async def on_start(self):
        print("[LWW] Map pokrenut")


class CrdtReplicator(Actor):
    def __init__(self, name, system):
        super().__init__(name, system)
        self.map_actor_name: str | None = None
        self.peers: list[str] = []

    async def default_behavior(self, message):
        if isinstance(message, Attach):
            self.map_actor_name = message.map_actor_name
            print(f"[CRDT-Rep] attached to map {self.map_actor_name}")
        elif isinstance(message, AddPeer):
            self.system.register_peer(message.remote_actor_name, message.host, message.port)
            if message.remote_actor_name not in self.peers:
                self.peers.append(message.remote_actor_name)
            print(f"[CRDT-Rep] added peer {message.remote_actor_name} @ {message.host}:{message.port}")
        elif isinstance(message, Replicate):
            for peer in list(self.peers):
                self.system.tell(peer, CrdtMerge(message.delta))
        elif isinstance(message, CrdtMerge):
            if self.map_actor_name:
                self.system.tell(self.map_actor_name, message)
        else:
            pass

    async def on_start(self):
        print("[CRDT-Rep] pokrenut")
