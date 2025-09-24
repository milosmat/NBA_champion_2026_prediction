from actor.actor_system import Actor
import asyncio
import time
from actor.supervisor import RestartRequest


class HealthPing:
    def __init__(self, monitor_name: str):
        self.monitor_name = monitor_name


class HealthAck:
    def __init__(self, actor_name: str):
        self.actor_name = actor_name


class CrashMe:
    pass


class HealthMonitor(Actor):
    def __init__(self, name, system, supervisor_name: str, actors_to_watch: list[str], ping_interval: float = 5.0, timeout: float = 10.0):
        super().__init__(name, system)
        self.supervisor_name = supervisor_name
        self.actors = list(actors_to_watch)
        self.ping_interval = float(ping_interval)
        self.timeout = float(timeout)
        self.last_ack = {a: 0.0 for a in self.actors}
        self._task = None

    async def on_start(self):
        print(f"[Health] monitor pokrenut; prati: {self.actors}")
        self._task = asyncio.create_task(self._loop())

    async def on_stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def default_behavior(self, message):
        if isinstance(message, HealthAck):
            self.last_ack[message.actor_name] = time.time()

    async def _loop(self):
        while self.alive:
            now = time.time()
            for a in self.actors:
                try:
                    self.system.tell(a, HealthPing(self.name))
                except Exception:
                    pass
            for a in list(self.actors):
                last = self.last_ack.get(a, 0.0)
                if last == 0.0:
                    continue
                if (now - last) > self.timeout:
                    print(f"[Health] {a} no-ack {now-last:.1f}s → restart request")
                    self.system.tell(self.supervisor_name, RestartRequest(actor_name=a, actor_class=None, args=None))
                    self.last_ack[a] = now
            await asyncio.sleep(self.ping_interval)
