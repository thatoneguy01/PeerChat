from storage.listeners import Listeners


def test_register_increments_count():
    listeners = Listeners()
    assert len(listeners) == 0
    listeners.register(lambda msg: None)
    listeners.register(lambda msg: None)
    assert len(listeners) == 2


def test_dispatch_calls_every_listener_in_registration_order():
    seen: list[str] = []
    listeners = Listeners()
    listeners.register(lambda msg: seen.append(f"a:{msg}"))
    listeners.register(lambda msg: seen.append(f"b:{msg}"))
    listeners.register(lambda msg: seen.append(f"c:{msg}"))

    listeners.dispatch("hi")

    assert seen == ["a:hi", "b:hi", "c:hi"]


def test_one_listener_raising_does_not_block_others():
    seen: list[str] = []
    listeners = Listeners()

    def good_before(msg):
        seen.append(f"before:{msg}")

    def bad(msg):
        raise RuntimeError("boom")

    def good_after(msg):
        seen.append(f"after:{msg}")

    listeners.register(good_before)
    listeners.register(bad)
    listeners.register(good_after)

    listeners.dispatch("x")
    assert seen == ["before:x", "after:x"]


def test_dispatch_with_no_listeners_is_safe():
    listeners = Listeners()
    listeners.dispatch("nothing-here")   # must not raise
