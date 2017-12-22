import asyncio
import functools
import logging

import bellows.types as t
from bellows.zigbee import util
from bellows.zigbee.zcl import foundation


LOGGER = logging.getLogger(__name__)


def deserialize(cluster_id, data):
    frame_control, data = data[0], data[1:]
    frame_type = frame_control & 0b0011
    direction = (frame_control & 0b1000) >> 3
    if frame_control & 0b0100:
        # Manufacturer specific value present
        data = data[2:]
    tsn, command_id, data = data[0], data[1], data[2:]

    is_reply = bool(direction)

    if frame_type == 1:
        # Cluster command
        if cluster_id not in Cluster._registry:
            LOGGER.debug("Ignoring unknown cluster ID 0x%04x",
                         cluster_id)
            return tsn, command_id + 256, is_reply, data
        cluster = Cluster._registry[cluster_id]
        # Cluster-specific command

        if direction:
            commands = cluster.client_commands
        else:
            commands = cluster.server_commands

        try:
            schema = commands[command_id][1]
            is_reply = commands[command_id][2]
        except KeyError:
            LOGGER.warning("Unknown cluster-specific command %s", command_id)
            return tsn, command_id + 256, is_reply, data

        # Bad hack to differentiate foundation vs cluster
        command_id = command_id + 256
    else:
        # General command
        try:
            schema = foundation.COMMANDS[command_id][1]
            is_reply = foundation.COMMANDS[command_id][2]
        except KeyError:
            LOGGER.warning("Unknown foundation command %s", command_id)
            return tsn, command_id, is_reply, data

    value, data = t.deserialize(data, schema)
    if data != b'':
        # TODO: Seems sane to check, but what should we do?
        LOGGER.warning("Data remains after deserializing ZCL frame")

    return tsn, command_id, is_reply, value


class Registry(type):
    def __init__(cls, name, bases, nmspc):  # noqa: N805
        super(Registry, cls).__init__(name, bases, nmspc)
        if hasattr(cls, 'cluster_id'):
            cls._registry[cls.cluster_id] = cls
        if hasattr(cls, 'cluster_id_range'):
            cls._registry_range[cls.cluster_id_range] = cls
        if hasattr(cls, 'attributes'):
            cls._attridx = {}
            for attrid, (attrname, datatype) in cls.attributes.items():
                cls._attridx[attrname] = attrid
        if hasattr(cls, 'server_commands'):
            cls._server_command_idx = {}
            for command_id, details in cls.server_commands.items():
                command_name, schema, is_reply = details
                cls._server_command_idx[command_name] = command_id
        if hasattr(cls, 'client_commands'):
            cls._client_command_idx = {}
            for command_id, details in cls.client_commands.items():
                command_name, schema, is_reply = details
                cls._client_command_idx[command_name] = command_id


class Cluster(util.LocalLogMixin, metaclass=Registry):
    """A cluster on an endpoint"""
    _registry = {}
    _registry_range = {}
    _server_command_idx = {}
    _client_command_idx = {}

    def __init__(self, endpoint):
        self._endpoint = endpoint
        self.is_input = None
        self._attr_cache = {}

    @classmethod
    def from_id(cls, endpoint, cluster_id):
        if cluster_id in cls._registry:
            return cls._registry[cluster_id](endpoint)
        else:
            for cluster_id_range, cluster in cls._registry_range.items():
                if cluster_id_range[0] <= cluster_id <= cluster_id_range[1]:
                    c = cluster(endpoint)
                    c.cluster_id = cluster_id
                    return c

        LOGGER.warning("Unknown cluster %s", cluster_id)
        c = cls(endpoint)
        c.cluster_id = cluster_id
        return c

    @util.retryable_request
    async def request(self, general, command_id, schema, *args, manufacturer=None):
        if len(schema) != len(args):
            self.error("Schema and args lengths do not match in request")
            error = asyncio.Future()
            error.set_exception(ValueError("Wrong number of parameters for request, expected %d argument(s)" % len(schema)))
            return error

        aps = self._endpoint.get_aps(self.cluster_id)
        if general:
            frame_control = 0x00
        else:
            frame_control = 0x01
        if manufacturer is not None:
            frame_control |= 0b0100
            manufacturer = manufacturer.to_bytes(2, 'little')
        else:
            manufacturer = b''
        data = bytes([frame_control]) + manufacturer + bytes([aps.sequence, command_id])
        data += t.serialize(args, schema)

        return await self._endpoint.device.request(aps, data)

    async def reply(self, general, command_id, schema, *args, manufacturer=None):
        if len(schema) != len(args):
            self.error("Schema and args lengths do not match in reply")
            error = asyncio.Future()
            error.set_exception(ValueError("Wrong number of parameters for reply, expected %d argument(s)" % len(schema)))
            return error

        aps = self._endpoint.get_aps(self.cluster_id)
        frame_control = 0b1000  # Cluster reply command
        if not general:
            frame_control |= 0x01
        if manufacturer is not None:
            frame_control |= 0b0100
            manufacturer = manufacturer.to_bytes(2, 'little')
        else:
            manufacturer = b''
        data = bytes([frame_control]) + manufacturer + bytes([aps.sequence, command_id])
        data += t.serialize(args, schema)

        return asyncio.ensure_future(self._endpoint.device.reply(aps, data))

    async def handle_message(self, is_reply, aps_frame, tsn, command_id, args):
        if is_reply:
            self.debug("Unexpected ZCL reply 0x%04x: %s", command_id, args)
            return

        self.debug("ZCL request 0x%04x: %s", command_id, args)
        if command_id <= 0xff:
            await self._endpoint.device.application.listener_event('zdo_command', self, aps_frame, tsn, command_id, args)
        else:
            # Unencapsulate bad hack
            command_id -= 256
            await self._endpoint.device.application.listener_event('cluster_command', self, aps_frame, tsn, command_id, args)
            await self.handle_cluster_request(aps_frame, tsn, command_id, args)
            return

        if command_id == 0x0a:  # Report attributes
            valuestr = ", ".join([
                "%s=%s" % (a.attrid, a.value.value) for a in args[0]
            ])
            self.debug("Attribute report received: %s", valuestr)
            for attr in args[0]:
                await self._update_attribute(attr.attrid, attr.value.value)
        else:
            await self.handle_cluster_general_request(aps_frame, tsn, command_id, args)

    async def handle_cluster_request(self, aps_frame, tsn, command_id, args):
        self.debug("No handler for cluster command %s", command_id)

    async def handle_cluster_general_request(self, aps_frame, tsn, command_id, args):
        self.debug("No handler for general command %s", command_id)

    async def read_attributes_raw(self, attributes, manufacturer=None):
        schema = foundation.COMMANDS[0x00][1]
        attributes = [t.uint16_t(a) for a in attributes]
        v = await self.request(True, 0x00, schema, attributes, manufacturer=manufacturer)
        return v

    async def read_attributes(self, attributes, allow_cache=False, raw=False, manufacturer=None):
        if raw:
            assert len(attributes) == 1
        success, failure = {}, {}
        attribute_ids = []
        orig_attributes = {}
        for attribute in attributes:
            if isinstance(attribute, str):
                attrid = self._attridx[attribute]
            else:
                attrid = attribute
            attribute_ids.append(attrid)
            orig_attributes[attrid] = attribute

        to_read = []
        if allow_cache:
            for idx, attribute in enumerate(attribute_ids):
                if attribute in self._attr_cache:
                    success[attributes[idx]] = self._attr_cache[attribute]
                else:
                    to_read.append(attribute)
        else:
            to_read = attribute_ids

        if not to_read:
            if raw:
                return success[attributes[0]]
            return success, failure

        result = await self.read_attributes_raw(to_read, manufacturer=manufacturer)
        if not isinstance(result[0], list):
            for attrid in to_read:
                orig_attribute = orig_attributes[attrid]
                failure[orig_attribute] = result[0]  # Assume default response
        else:
            for record in result[0]:
                orig_attribute = orig_attributes[record.attrid]
                if record.status == 0:
                    await self._update_attribute(record.attrid, record.value.value)
                    success[orig_attribute] = record.value.value
                else:
                    failure[orig_attribute] = record.status

        if raw:
            # KeyError is an appropriate exception here, I think.
            return success[attributes[0]]
        return success, failure

    async def write_attributes(self, attributes, is_report=False):
        args = []
        for attrid, value in attributes.items():
            if isinstance(attrid, str):
                attrid = self._attridx[attrid]
            if attrid not in self.attributes:
                self.error("%d is not a valid attribute id", attrid)
                continue

            if is_report:
                a = foundation.ReadAttributeRecord()
                a.status = 0
            else:
                a = foundation.Attribute()

            a.attrid = t.uint16_t(attrid)
            a.value = foundation.TypeValue()

            try:
                python_type = self.attributes[attrid][1]
                a.value.type = t.uint8_t(foundation.DATA_TYPE_IDX[python_type])
                a.value.value = python_type(value)
                args.append(a)
            except ValueError as e:
                self.error(str(e))

        if is_report:
            schema = foundation.COMMANDS[0x01][1]
            return await self.reply(False, 0x01, schema, args)
        else:
            schema = foundation.COMMANDS[0x02][1]
            return await self.request(True, 0x02, schema, args)

    async def bind(self):
        return await self._endpoint.device.zdo.bind(self._endpoint.endpoint_id, self.cluster_id)

    async def unbind(self):
        return await self._endpoint.device.zdo.unbind(self._endpoint.endpoint_id, self.cluster_id)

    async def configure_reporting(self, attribute, min_interval, max_interval, reportable_change):
        schema = foundation.COMMANDS[0x06][1]
        cfg = foundation.AttributeReportingConfig()
        cfg.direction = 0
        if isinstance(attribute, str):
            attribute = self._attridx[attribute]
        cfg.attrid = attribute
        cfg.datatype = foundation.DATA_TYPE_IDX.get(
            self.attributes.get(attribute, (None, None))[1],
            None)
        cfg.min_interval = min_interval
        cfg.max_interval = max_interval
        cfg.reportable_change = reportable_change
        return await self.request(True, 0x06, schema, [cfg])

    async def command(self, command, *args, manufacturer=None):
        schema = self.server_commands[command][1]
        return await self.request(False, command, schema, *args, manufacturer=manufacturer)

    async def client_command(self, command, *args):
        schema = self.client_commands[command][1]
        return await self.reply(True, command, schema, *args)

    @property
    def name(self):
        return self.__class__.__name__

    @property
    def endpoint(self):
        return self._endpoint

    @property
    def commands(self):
        return list(self._server_command_idx.keys())

    async def _update_attribute(self, attrid, value):
        self._attr_cache[attrid] = value
        await self._endpoint.device.application.listener_event('attribute_updated', self, attrid, value)

    async def update_attribute_local(self, attr, value):
        if isinstance(attr, str):
            attr = self._attridx[attr]
        self.debug("Updating attribute locally: %s = %s", attr, value)
        await self._update_attribute(attr, value)

    def log(self, lvl, msg, *args):
        msg = '[0x%04x:%s:0x%04x] ' + str(msg)
        args = (
            self._endpoint.device.nwk,
            self._endpoint.endpoint_id,
            self.cluster_id,
        ) + args
        return LOGGER.log(lvl, msg, *args)

    def __getattr__(self, name):
        try:
            if name in self._client_command_idx:
                return functools.partial(
                    self.client_command,
                    self._client_command_idx[name],
                )
            else:
                return functools.partial(
                    self.command,
                    self._server_command_idx[name],
                )
        except KeyError:
            raise AttributeError("No such command name: %s" % (name, ))

    def __getitem__(self, key):
        return self.read_attributes([key], allow_cache=True, raw=True)


# Import to populate the registry
from . import clusters  # noqa: F401, F402

CLUSTER_ID = util.Dotdict({c.ep_attribute: c.cluster_id
                           for m in [clusters.closures, clusters.general, clusters.homeautomation,
                                     clusters.hvac, clusters.lighting, clusters.lightlink,
                                     clusters.manufacturer_specific, clusters.measurement,
                                     clusters.protocol, clusters.security, clusters.smartenergy]
                           for c in m.__dict__.values() if isinstance(c, type) and issubclass(c, Cluster) and hasattr(c, 'cluster_id')})
