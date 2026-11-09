"""Run with: uv run --no-project python test/check_blanket_zones.py."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace


class PropertyEntity:
    def __init__(self, miot_device, spec):
        self.miot_device = miot_device
        self.spec = spec
        self._value = "0-0"
        self._attr_unique_id = "blanket"
        self.entity_id = "number.blanket"
        self.name = "blanket"

    def async_write_ha_state(self):
        self.displayed = self.native_value

    async def get_property_async(self):
        await asyncio.sleep(0)
        return self.miot_device.raw

    async def set_property_async(self, value):
        await asyncio.sleep(0)  # Let the other zone start before this write ends.
        self.miot_device.writes.append(value)
        self.miot_device.raw = value
        self._value = value
        self.async_write_ha_state()


async def check_zone_transitions():
    source = (Path(__file__).resolve().parents[1] / "custom_components"
              / "xiaomi_home" / "number.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    zone_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "ElectricBlanketZoneNumber")
    namespace = {"asyncio": asyncio, "MIoTPropertyEntity": PropertyEntity,
                 "NumberEntity": type("NumberEntity", (), {})}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), zone_class], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    zone_number = namespace["ElectricBlanketZoneNumber"]
    switch_source = source.with_name("switch.py")
    switch_tree = ast.parse(switch_source.read_text(encoding="utf-8"))
    switch_class = next(node for node in switch_tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "Switch")
    namespace["SwitchEntity"] = type("SwitchEntity", (), {})
    switch_module = ast.Module(body=[module.body[0], switch_class], type_ignores=[])
    exec(compile(ast.fix_missing_locations(switch_module), str(switch_source), "exec"),
         namespace)
    switch_type = namespace["Switch"]

    async def read_power(**kwargs):
        return device.power

    power_prop = SimpleNamespace(name="on", service=SimpleNamespace(iid=2),
                                 iid=1, value_format=bool, device_class=None)
    device = SimpleNamespace(did="blanket", raw="0-0", power=True, writes=[],
                             prop_list={"switch": [power_prop]},
                             miot_client=SimpleNamespace(get_prop_async=read_power))
    peers = []
    a = zone_number(device, SimpleNamespace(), 0, peers)
    b = zone_number(device, SimpleNamespace(), 1, peers)

    # No property notifications: concurrent consumers must preserve both writes.
    await asyncio.gather(a.async_set_native_value(3), b.async_set_native_value(4))
    assert device.writes == ["3-0", "3-4"], device.writes
    assert (a.displayed, b.displayed) == (3, 4)
    await a.async_set_native_value(0)
    assert device.raw == "0-4"
    await b.async_set_native_value(5)
    assert device.raw == "0-5"

    # Overall-gear / physical changes refresh both stale sliders without a write.
    a._value, b._value = "0-3", "3-0"
    device.raw = "6-6"
    before = list(device.writes)
    await a.async_update()
    assert (a.displayed, b.displayed) == (6, 6)
    assert device.writes == before

    # A stale slider must not overwrite the device's other zone even before poll.
    a._value, b._value = "0-3", "3-0"
    await a.async_set_native_value(2)
    assert device.raw == "2-6"
    await b.async_set_native_value(4)
    assert device.raw == "2-4"
    device.raw = "6-6"
    await a.async_update()
    before = list(device.writes)

    def forbidden(*args, **kwargs):
        raise AssertionError("switch turn_off must not discover or write blanket zones")

    power_writes = []
    other_writes = []

    async def write_other(value):
        other_writes.append(value)

    other = switch_type(SimpleNamespace(), power_prop)
    other._value = True
    other.hass = SimpleNamespace(
        states=SimpleNamespace(async_entity_ids=forbidden),
        services=SimpleNamespace(async_call=forbidden))
    other.set_property_async = write_other
    await other.async_turn_off()
    assert other_writes == [False]
    assert device.writes == before

    async def write_power(value):
        power_writes.append(value)
        device.power = value
        master._value = value

    master = switch_type(device, power_prop)
    master._value = True
    master.hass = other.hass
    master.set_property_async = write_power

    # Master off masks the display only, so changing a zone preserves its peer.
    await master.async_turn_off()
    assert power_writes == [False]
    await a.async_update()
    assert (a.displayed, b.displayed) == (0, 0)
    assert a._value == b._value == "6-6"
    assert device.writes == before
    await a.async_set_native_value(2)
    assert device.raw == "2-6"
    assert (a.displayed, b.displayed) == (0, 0)
    device.power = True
    await b.async_update()
    assert (a.displayed, b.displayed) == (2, 6)


if __name__ == "__main__":
    asyncio.run(check_zone_transitions())
    print("blanket switch isolation, zone preservation and transitions: OK")
