import asyncio
import enum
import logging
import bellows.types as t
from bellows.zigbee.zcl import foundation
from bellows.zigbee.device import Device
from bellows.zigbee.endpoint import Status as EpStatus
# from bellows.zigbee.profiles.zll import PROFILE_ID as ZLL_PROFILE_ID
from bellows.zigbee.profiles.zha import PROFILE_ID as ZHA_PROFILE_ID

LOGGER = logging.getLogger(__name__)


class Xiaomi(Device):
    async def _discover_endpoints(self):
        LOGGER.info("No point looking for endpoints on Xiaomi")
        ep = self.add_endpoint(1)
        ep.status = EpStatus.INITIALIZED
        return True

    async def attribute_updated(self, cluster, attrid, data):
        if cluster.cluster_id == 0 and attrid == 5:  # model name
            if not getattr(cluster, "xiaomi_bound", False):
                model = data.decode()[5:]
                LOGGER.info('Xiaomi model: %s', model)
                if model.startswith("sensor_magnet"):
                    LOGGER.info('Found Xiaomi door sensor')
                    res = await cluster.bind()
                    LOGGER.warning('Bind response is: %r', res)
                    cluster.xiaomi_bound = True
        elif attrid == 0xFF01:  # Heartbeat
            while data:
                xiaomi_type = data[0]
                value, data = foundation.TypeValue.deserialize(data[1:])
                value = value.value

                if xiaomi_type == 0x01:
                    await cluster.endpoint.power.update_attribute_local(
                        "battery_percentage_remaining", (value - 2600) / (3200 - 2600))
                    await cluster.endpoint.power.update_attribute_local(
                        "battery_voltage", value / 1000)
                elif xiaomi_type == 0x03:
                    await cluster.endpoint.temperature.update_attribute_local(
                        "measured_value", value)
                elif xiaomi_type == 0x64:
                    await cluster.endpoint.on_off.update_attribute_local(
                        "on_off", value)

            return None
        return data


class IKEA(Device):
    def get_aps(self, profile, cluster, endpoint):
        aps = Device.get_aps(self, profile, cluster, endpoint)
        if profile != 0:
            aps.profileId = t.uint16_t(ZHA_PROFILE_ID)
        return aps


class VENDOR(enum.IntEnum):
    IKEA = 0x117c
    XIAOMI = 0x1037


SPECIALIZED_DEVICES = {
    VENDOR.IKEA: IKEA,
    VENDOR.XIAOMI: Xiaomi,
}


async def get_device(dev):
    """Get device."""
    for i in range(3):
        manufacturer = await dev.get_manufacturer_code()
        if manufacturer:
            break
        asyncio.sleep(1)

    if manufacturer in SPECIALIZED_DEVICES:
        dev = SPECIALIZED_DEVICES[manufacturer](dev._application, dev._ieee, dev.nwk, manufacturer)

    return dev
