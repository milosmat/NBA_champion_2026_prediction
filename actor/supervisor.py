from actor.actor_system import Actor

class RestartRequest:
    def __init__(self, actor_name, actor_class, args):
        self.actor_name = actor_name
        self.actor_class = actor_class
        self.args = args

class Supervisor(Actor):
    def __init__(self, name, system):
        super().__init__(name, system)
        self.children = {}

    def watch(self, actor_name, actor_class, args):
        """Dodaj child aktora pod nadzor"""
        self.children[actor_name] = (actor_class, args)

    async def default_behavior(self, message):
        if isinstance(message, RestartRequest):
            print(f"[Supervisor] Restartuje {message.actor_name}")
            actor_class = message.actor_class
            args = message.args
            if actor_class is None or args is None:
                reg = self.children.get(message.actor_name)
                if reg:
                    actor_class, args = reg
            if actor_class is None:
                print(f"[Supervisor] nema klasifikacije za {message.actor_name}, preskačem restart")
                return
            self.system.create_actor(message.actor_name, lambda name, sys: actor_class(name, sys, *args))
