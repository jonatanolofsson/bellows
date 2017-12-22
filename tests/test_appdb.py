import asyncio
import util
import os
from unittest import mock

import pytest

import bellows.types as t
from bellows.zigbee.application import ControllerApplication
from bellows.zigbee import profiles
import bellows.zigbee.device
import bellows.zigbee.endpoint


async def make_app(database_file):
    ezsp = mock.MagicMock()
    obj = ControllerApplication(ezsp, database_file)
    await obj._dblistener.load()
    return obj


@pytest.fixture
def ieee(init=0):
    return t.EmberEUI64(map(t.uint8_t, range(init, init + 8)))


@util.async
async def test_database(tmpdir, ieee):
    db = os.path.join(str(tmpdir), 'test.db')
    app = await make_app(db)
    # TODO: Leaks a task on dev.initialize, I think?
    app._joining[99] = None
    await app._handle_join(99, ieee, None, None, 0, no_init=True, manufacturer=0xDEAD)
    app._joining[99] = None
    await app._handle_join(99, ieee, None, None, 0, no_init=True, manufacturer=0xDEAD)

    dev = await app.get_device(ieee)
    dev.status = bellows.zigbee.device.Status.INITIALIZING
    dev._manufacturer_code = 0xDEAD
    ep = dev.add_endpoint(1)
    ep.profile_id = 260
    ep.device_type = profiles.zha.DeviceType.PUMP
    ep.status = bellows.zigbee.endpoint.Status.INITIALIZED
    ep = dev.add_endpoint(2)
    ep.profile_id = 260
    ep.device_type = 0xfffd  # Invalid
    ep.status = bellows.zigbee.endpoint.Status.INITIALIZED
    clus = ep.add_cluster(0, True)
    ep.add_cluster(1, False)
    ep = dev.add_endpoint(3)
    ep.profile_id = 49246
    ep.device_type = profiles.zll.DeviceType.COLOR_LIGHT
    ep.status = bellows.zigbee.endpoint.Status.INITIALIZED
    dev.status = bellows.zigbee.device.Status.INITIALIZED
    await app.listener_event('device_initialized', dev)
    await clus._update_attribute(0, 99)

    # Everything should've been saved - check that it re-loads
    app2 = await make_app(db)
    dev = await app2.get_device(ieee)
    assert dev.endpoints[1].device_type == profiles.zha.DeviceType.PUMP
    assert dev.endpoints[2].device_type == 0xfffd
    assert dev.endpoints[2].in_clusters[0]._attr_cache[0] == 99
    assert dev.endpoints[2].out_clusters[1].cluster_id == 1
    assert dev.endpoints[3].device_type == profiles.zll.DeviceType.COLOR_LIGHT

    await app._handle_leave(99, ieee)

    app2 = await make_app(db)
    assert ieee in app2.devices

    async def mockleave(*args, **kwargs):
        return [0]

    app2.devices[ieee].zdo.leave = mockleave
    await app2.remove(ieee)
    assert ieee not in app2.devices

    app3 = await make_app(db)
    assert ieee not in app3.devices

    os.unlink(db)
