import re
import util

from unittest import mock
import bellows.zigbee.endpoint
import bellows.zigbee.zcl as zcl


def test_registry():
    for cluster_id, cluster in zcl.Cluster._registry.items():
        assert 0 <= getattr(cluster, 'cluster_id', -1) <= 65535
        assert cluster_id == cluster.cluster_id
        assert issubclass(cluster, zcl.Cluster)


def test_attributes():
    for cluster_id, cluster in zcl.Cluster._registry.items():
        for attrid, attrspec in cluster.attributes.items():
            assert 0 <= attrid <= 0xffff
            assert isinstance(attrspec, tuple)
            assert isinstance(attrspec[0], str)
            assert hasattr(attrspec[1], 'serialize')
            assert hasattr(attrspec[1], 'deserialize')


def _test_commands(cmdattr):
    for cluster_id, cluster in zcl.Cluster._registry.items():
        print(cluster.cluster_id)
        for cmdid, cmdspec in getattr(cluster, cmdattr).items():
            assert 0 <= cmdid <= 0xff
            assert isinstance(cmdspec, tuple), "Cluster %s" % (cluster_id, )
            assert len(cmdspec) == 3
            assert isinstance(cmdspec[0], str)
            assert isinstance(cmdspec[1], tuple)
            assert isinstance(cmdspec[2], bool)
            for t in cmdspec[1]:
                assert hasattr(t, 'serialize')
                assert hasattr(t, 'deserialize')


def test_server_commands():
    _test_commands('server_commands')


def test_client_commands():
    _test_commands('client_commands')


def test_ep_attributes():
    seen = set()
    for cluster_id, cluster in zcl.Cluster._registry.items():
        assert isinstance(cluster.ep_attribute, str)
        assert re.match('^[a-z_]+$', cluster.ep_attribute)
        assert cluster.ep_attribute not in seen
        seen.add(cluster.ep_attribute)


@util.async
async def test_time_cluster():
    ep = mock.MagicMock()
    ep.device = mock.MagicMock()
    t = zcl.Cluster._registry[0x000a](ep)

    aps_frame = 0
    tsn = 0

    await t.handle_cluster_request(aps_frame, tsn, 1, [[0]])
    assert ep.device.reply.call_count == 0

    await t.handle_cluster_request(aps_frame, tsn, 0, [[0]])
    assert ep.device.reply.call_count == 1
    assert ep.device.reply.call_args[0][1][3] == 0

    await t.handle_cluster_request(aps_frame, tsn, 0, [[1]])
    assert ep.device.reply.call_count == 2
    assert ep.device.reply.call_args[0][1][3] == 1

    await t.handle_cluster_request(aps_frame, tsn, 0, [[2]])
    assert ep.device.reply.call_count == 3
    assert ep.device.reply.call_args[0][1][3] == 2

    await t.handle_cluster_request(aps_frame, tsn, 0, [[0, 1, 2]])
    assert ep.device.reply.call_count == 4
    assert ep.device.reply.call_args[0][1][3] == 0
