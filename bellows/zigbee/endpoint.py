import asyncio
import enum
import logging

import bellows.zigbee.appdb
import bellows.zigbee.util
import bellows.zigbee.zcl
import bellows.zigbee.zha

LOGGER = logging.getLogger(__name__)


class Status(enum.IntEnum):
    """The status of an Endpoint"""
    # No initialization is done
    NEW = 0
    # Endpoint information (device type, clusters, etc) init done
    ZDO_INIT = 1


class Endpoint(bellows.zigbee.util.LocalLogMixin, bellows.zigbee.util.ListenableMixin):
    """An endpoint on a device on the network"""
    def __init__(self, device, endpoint_id):
        self._device = device
        self._endpoint_id = endpoint_id
        self.clusters = {}
        self.status = Status.NEW
        self._listeners = {}

    @asyncio.coroutine
    def initialize(self):
        self.info("Discovering endpoint information")
        sdr = yield from self._device.zdo.request(
            0x0004,
            self._device._nwk,
            self._endpoint_id,
        )
        if sdr[0] != 0:
            # TODO: Handle
            self.warn("Failed ZDO request during device initialization")
            return

        self.info("Discovered endpoint information: %s", sdr[2])

        sd = sdr[2]
        self.profile_id = sd.profile
        self.device_type = sd.device_type
        if self.profile_id == 260:
            try:
                self.device_type = bellows.zigbee.zha.DeviceType(self.device_type)
            except:
                pass

        for cluster in sd.input_clusters:
            self.add_cluster(cluster)

        self.output_clusters = sd.output_clusters

        self.status = Status.ZDO_INIT

    def add_cluster(self, cluster_id):
        """Adds a device's input cluster

        (a server cluster supported by the device)
        """
        cluster = bellows.zigbee.zcl.Cluster.from_id(self, cluster_id)
        self.clusters[cluster_id] = cluster
        listener = bellows.zigbee.appdb.ClusterPersistingListener(
            self._device._application._dblistener,
            cluster,
        )
        cluster.add_listener(listener)
        return cluster

    def get_aps(self, cluster):
        assert self.status != Status.NEW
        return self._device.get_aps(
            profile=self.profile_id,
            cluster=cluster,
            endpoint=self._endpoint_id,
        )

    def handle_message(self, is_reply, aps_frame, tsn, command_id, args):
        try:
            self.clusters[aps_frame.clusterId].handle_message(is_reply,
                                                              aps_frame, tsn,
                                                              command_id, args)
        except KeyError:
            self.warn("Message on unknown cluster 0x%04x", aps_frame.clusterId)
            self.listener_event("unknown_cluster_message", is_reply,
                                command_id, args)

    def log(self, lvl, msg, *args):
        msg = '[0x%04x:%s] ' + msg
        args = (self._device._nwk, self._endpoint_id) + args
        return LOGGER.log(lvl, msg, *args)