import enum
import logging
from bellows.zigbee.zcl import foundation, Cluster
from bellows.zigbee.device import Device
# from bellows.zigbee.profiles.zll import PROFILE_ID as ZLL_PROFILE_ID
from bellows.zigbee.profiles.zha import PROFILE_ID as ZHA_PROFILE_ID

LOGGER = logging.getLogger(__name__)

class Xiaomi:
    @staticmethod
    async def device_initialized(device):
        device[1].on_off.add_listener(Xiaomi)

    @staticmethod
    async def attribute_updated(cluster, attrid, data):
        if cluster.cluster_id == 0 and attrid == 5:  # model name
            if not getattr(cluster, "xiaomi_bound", False):
                model = data.decode()[5:]
                LOGGER.info('Xiaomi model: %s', model)
                if model.startswith("sensor_magnet"):
                    LOGGER.info('Found Xiaomi door sensor')
                    res = await cluster.bind()
                    LOGGER.warning('Bind response is: %r', res)
                    # res = await cluster.write_attributes({'cie_addr': device.application.ieee})
                    # LOGGER.info('Attribute write response is: %r', res)
                    res = await cluster.configure_reporting(attrid=0, min_interval=11 * 60 * 60, max_interval=12 * 60 * 60, reportable_change=1)
                    LOGGER.warning('Configure reporting response is: %r', res)
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
        aps.profileId = ZHA_PROFILE_ID
        return aps


class VENDOR(enum.IntEnum):
    IKEA = 0x117c
    XIAOMI = 0x1037


SPECIALIZED_LISTENERS = {
    VENDOR.XIAOMI: Xiaomi,
}


SPECIALIZED_DEVICES = {
    VENDOR.IKEA: IKEA,
}


class SpecializationListener:
    def __getattr__(self, name):
        async def inner(first, *args, **kwargs):
            mcode = None
            if isinstance(first, Cluster):
                mcode = first.endpoint.device.manufacturer_code
            elif isinstance(first, Device):
                mcode = first.manufacturer_code
            if mcode is None or mcode not in SPECIALIZED_LISTENERS:
                return
            method = getattr(SPECIALIZED_LISTENERS[mcode], name, None)
            if method is not None:
                await method(first, *args, **kwargs)
        return inner


async def get_device(application, ieee, nwk, manufacturer=None):
    """Get device."""
    dev = None
    if manufacturer is None:
        dev = Device(application, ieee, nwk, manufacturer)
        await dev.initialize(silent=True)
        manufacturer = dev.manufacturer_code

    if manufacturer in SPECIALIZED_DEVICES:
        return SPECIALIZED_DEVICES[manufacturer](application, ieee, nwk, manufacturer)

    if dev is None:
        dev = Device(application, ieee, nwk, manufacturer)
    return dev
