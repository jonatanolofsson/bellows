import enum
import logging

import bellows.zigbee.profiles
import bellows.zigbee.util as zutil
import bellows.zigbee.zcl
from bellows.zigbee.zdo import CLUSTER_ID as ZDO_CLUSTER_ID
from bellows.zigbee.zcl import CLUSTER_ID as ZCL_CLUSTER_ID

LOGGER = logging.getLogger(__name__)


class Status(enum.IntEnum):
    """The status of an Endpoint"""
    # No initialization is done
    NEW = 0
    # Initing
    INITIALIZING = 1
    # Endpoint information (device type, clusters, etc) init done
    ZDO_INIT = 2
    # Finished
    INITIALIZED = 100


class Endpoint(zutil.LocalLogMixin, zutil.ListenableMixin):
    """An endpoint on a device on the network"""
    def __init__(self, device, endpoint_id):
        self._device = device
        self._endpoint_id = endpoint_id
        self.in_clusters = {}
        self.out_clusters = {}
        self._cluster_attr = {}
        self.status = Status.NEW
        self._listeners = {}

    async def initialize(self):
        # if self.status != Status.NEW:
            # return
        self.status = Status.INITIALIZING

        self.info("Discovering endpoint information")
        try:
            sdr = await self._device.zdo.request(
                ZDO_CLUSTER_ID.Simple_Desc_req,
                self._device.nwk,
                self._endpoint_id,
                tries=3,
                delay=2,
            )
            if sdr[0] != 0:
                raise Exception("Failed to retrieve service descriptor: %s", sdr)
        except Exception as exc:
            self.warn("Failed ZDO request during device initialization: %s", exc)
            return

        self.info("Discovered endpoint information: %s", sdr[2])
        sd = sdr[2]
        self.profile_id = sd.profile
        self.device_type = sd.device_type
        try:
            self.device_type = bellows.zigbee.profiles.PROFILES[self.profile_id].DeviceType(self.device_type)
        except ValueError:
            pass

        for cluster in sd.input_clusters:
            self.add_cluster(cluster, True)
        for cluster in sd.output_clusters:
            self.add_cluster(cluster, False)

        self.status = Status.INITIALIZED

    def add_cluster(self, cluster_id, is_input):
        """Adds an endpoint's cluster

        (a server cluster supported by the device)
        """
        clusters = self.in_clusters if is_input else self.out_clusters
        if cluster_id in clusters:
            return clusters[cluster_id]

        cluster = bellows.zigbee.zcl.Cluster.from_id(self, cluster_id)
        cluster.is_input = is_input
        clusters[cluster_id] = cluster

        if hasattr(cluster, 'ep_attribute'):
            self._cluster_attr[cluster.ep_attribute] = cluster

        return cluster

    def get_aps(self, cluster):
        assert self.status != Status.NEW

        return self._device.get_aps(
            profile=self.profile_id,
            cluster=cluster,
            endpoint=self._endpoint_id,
        )

    async def handle_message(self, is_reply, aps_frame, tsn, command_id, args):
        handler = None
        if aps_frame.clusterId in self.in_clusters:
            handler = self.in_clusters[aps_frame.clusterId].handle_message
        elif aps_frame.clusterId in self.out_clusters:
            handler = self.out_clusters[aps_frame.clusterId].handle_message
        else:
            self.warn("Message on unknown cluster 0x%04x (0x%04x: %s)", aps_frame.clusterId, self._device.nwk, self._endpoint_id)
            await self.listener_event("unknown_cluster_message", is_reply,
                                      command_id, args)
            self.add_cluster(aps_frame.clusterId, True)
            await self._device._application.listener_event('device_updated', self.device)
            handler = self.in_clusters[aps_frame.clusterId].handle_message

        await handler(is_reply, aps_frame, tsn, command_id, args)

    def log(self, lvl, msg, *args):
        msg = '[0x%04x:%s] ' + msg
        args = (self._device.nwk, self._endpoint_id) + args
        return LOGGER.log(lvl, msg, *args)

    @property
    def device(self):
        return self._device

    @property
    def endpoint_id(self):
        return self._endpoint_id

    def __getattr__(self, name):
        try:
            if name not in self._cluster_attr:
                self.add_cluster(ZCL_CLUSTER_ID[name], True)
            return self._cluster_attr[name]
        except KeyError:
            raise AttributeError
