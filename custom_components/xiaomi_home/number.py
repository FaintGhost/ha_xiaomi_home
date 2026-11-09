# -*- coding: utf-8 -*-
"""
Copyright (C) 2024 Xiaomi Corporation.

The ownership and intellectual property rights of Xiaomi Home Assistant
Integration and related Xiaomi cloud service API interface provided under this
license, including source code and object code (collectively, "Licensed Work"),
are owned by Xiaomi. Subject to the terms and conditions of this License, Xiaomi
hereby grants you a personal, limited, non-exclusive, non-transferable,
non-sublicensable, and royalty-free license to reproduce, use, modify, and
distribute the Licensed Work only for your use of Home Assistant for
non-commercial purposes. For the avoidance of doubt, Xiaomi does not authorize
you to use the Licensed Work for any other purpose, including but not limited
to use Licensed Work to develop applications (APP), Web services, and other
forms of software.

You may reproduce and distribute copies of the Licensed Work, with or without
modifications, whether in source or object form, provided that you must give
any other recipients of the Licensed Work a copy of this License and retain all
copyright and disclaimers.

Xiaomi provides the Licensed Work on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied, including, without
limitation, any warranties, undertakes, or conditions of TITLE, NO ERROR OR
OMISSION, CONTINUITY, RELIABILITY, NON-INFRINGEMENT, MERCHANTABILITY, or
FITNESS FOR A PARTICULAR PURPOSE. In any event, you are solely responsible
for any direct, indirect, special, incidental, or consequential damages or
losses arising from the use or inability to use the Licensed Work.

Xiaomi reserves all rights not expressly granted to you in this License.
Except for the rights expressly granted by Xiaomi under this License, Xiaomi
does not authorize you in any form to use the trademarks, copyrights, or other
forms of intellectual property rights of Xiaomi and its affiliates, including,
without limitation, without obtaining other written permission from Xiaomi, you
shall not use "Xiaomi", "Mijia" and other words related to Xiaomi or words that
may make the public associate with Xiaomi in any form to publicize or promote
the software or hardware devices that use the Licensed Work.

Xiaomi has the right to immediately terminate all your authorization under this
License in the event:
1. You assert patent invalidation, litigation, or other claims against patents
or other intellectual property rights of Xiaomi or its affiliates; or,
2. You make, have made, manufacture, sell, or offer to sell products that knock
off Xiaomi or its affiliates' products.

Number entities for Xiaomi Home.
"""
from __future__ import annotations
import logging
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.number import NumberEntity

from .miot.const import DOMAIN
from .miot.miot_spec import MIoTSpecProperty
from .miot.miot_device import MIoTDevice, MIoTPropertyEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a config entry."""
    device_list: list[MIoTDevice] = hass.data[DOMAIN]['devices'][
        config_entry.entry_id]

    new_entities = []
    for miot_device in device_list:
        for prop in miot_device.prop_list.get('number', []):
            new_entities.append(Number(miot_device=miot_device, spec=prop))
        # Electric blankets expose A/B target gears as one "A-B" string
        # property (custom.a-b-gears). Split it into two HA number entities.
        for service in miot_device.spec_instance.services:
            if service.name != 'custom':
                continue
            for prop in service.properties:
                if prop.name != 'a-b-gears':
                    continue
                for zone in (0, 1):
                    new_entities.append(
                        ElectricBlanketZoneNumber(
                            miot_device=miot_device, spec=prop, zone=zone))

    if new_entities:
        async_add_entities(new_entities)


class Number(MIoTPropertyEntity, NumberEntity):
    """Number entities for Xiaomi Home."""

    def __init__(self, miot_device: MIoTDevice, spec: MIoTSpecProperty) -> None:
        """Initialize the Notify."""
        super().__init__(miot_device=miot_device, spec=spec)
        # Set device_class
        self._attr_device_class = spec.device_class
        # Set unit
        if self.spec.external_unit:
            self._attr_native_unit_of_measurement = self.spec.external_unit
        # Set icon
        if self.spec.icon and not self.device_class:
            self._attr_icon = self.spec.icon
        # Set value range
        if self._value_range:
            self._attr_native_min_value = self._value_range.min_
            self._attr_native_max_value = self._value_range.max_
            self._attr_native_step = self._value_range.step

    @property
    def native_value(self) -> Optional[float]:
        """Return the current value of the number."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        await self.set_property_async(value=value)


class ElectricBlanketZoneNumber(MIoTPropertyEntity, NumberEntity):
    """One A/B zone of an electric blanket's combined 'A-B' gear string."""

    def __init__(
        self, miot_device: MIoTDevice, spec: MIoTSpecProperty, zone: int
    ) -> None:
        """Initialize an A or B zone gear number."""
        super().__init__(miot_device=miot_device, spec=spec)
        self._zone = zone
        zone_name = 'A' if zone == 0 else 'B'
        self._attr_unique_id = f'{self._attr_unique_id}_zone_{zone_name}'
        self.entity_id = f'{self.entity_id}_zone_{zone_name.lower()}'
        self._attr_name = f'{zone_name}区档位'
        self._attr_icon = 'mdi:radiator'
        self._attr_native_min_value = 0
        self._attr_native_max_value = 6
        self._attr_native_step = 1
        self._power_prop: Optional[MIoTSpecProperty] = None
        for prop in miot_device.prop_list.get('switch', []):
            if prop.name == 'on':
                self._power_prop = prop
                break
        if not self._power_prop:
            for service in miot_device.spec_instance.services:
                for prop in service.properties:
                    if prop.name == 'on':
                        self._power_prop = prop
                        break
                if self._power_prop:
                    break
        self._power_on: Optional[bool] = None
        self._power_sub_id = 0
        _LOGGER.warning(
            'blanket zone init, %s, power prop: %s',
            self.entity_id,
            self._power_prop.name if self._power_prop else None)
        if not self._power_prop:
            _LOGGER.warning(
                'blanket zone services, %s',
                [(service.name, service.iid,
                  [prop.name for prop in service.properties])
                 for service in miot_device.spec_instance.services])

    async def async_added_to_hass(self) -> None:
        """Subscribe to the blanket power state and load its current value."""
        await super().async_added_to_hass()
        if not self._power_prop:
            return
        self._power_sub_id = self.miot_device.sub_property(
            handler=self.__on_power_changed,
            siid=self._power_prop.service.iid,
            piid=self._power_prop.iid)
        try:
            value = await self.miot_device.miot_client.get_prop_async(
                did=self.miot_device.did,
                siid=self._power_prop.service.iid,
                piid=self._power_prop.iid)
        except Exception:  # pylint: disable=broad-exception-caught
            value = None
        if value is not None:
            self._power_on = bool(self._power_prop.value_format(value))
            _LOGGER.warning(
                'blanket zone power initial, %s, raw=%r, on=%r',
                self.entity_id, value, self._power_on)
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from the blanket power state."""
        if self._power_prop and self._power_sub_id:
            self.miot_device.unsub_property(
                siid=self._power_prop.service.iid,
                piid=self._power_prop.iid,
                sub_id=self._power_sub_id)
            self._power_sub_id = 0
        await super().async_will_remove_from_hass()

    def __on_power_changed(self, params: dict, ctx: Any) -> None:
        if not self._power_prop:
            return
        self._power_on = bool(self._power_prop.value_format(params.get('value')))
        _LOGGER.warning(
            'blanket zone power changed, %s, raw=%r, on=%r',
            self.entity_id, params.get('value'), self._power_on)
        self.async_write_ha_state()

    def _get_gears(self, raw: Any) -> Optional[tuple[int, int]]:
        """Parse the blanket's 'A-B' gear string."""
        if not isinstance(raw, str):
            return None
        parts = raw.split('-')
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    @property
    def native_value(self) -> Optional[float]:
        """Return this zone's current gear, or 0 when the blanket is off."""
        if self._power_on is False:
            return 0
        gears = self._get_gears(self._value)
        if gears is None:
            return None
        return gears[self._zone]

    async def async_set_native_value(self, value: float) -> None:
        """Update this zone's gear while preserving the other zone."""
        gear = int(value)
        if not 0 <= gear <= 6:
            raise ValueError(f'gear out of range: {value}')
        gears = self._get_gears(self._value)
        if gears is None:
            gears = self._get_gears(await self.get_property_async())
        if gears is None:
            raise RuntimeError(
                f'cannot parse a-b-gears, {self.entity_id}, {self.name}')
        a_gear, b_gear = gears
        if self._zone == 0:
            a_gear = gear
        else:
            b_gear = gear
        await self.set_property_async(value=f'{a_gear}-{b_gear}')
