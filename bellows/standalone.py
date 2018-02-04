"""Standalone websocket interface to bellows."""

from aiohttp import web
import asyncio
import functools
import json
import logging
import websockets


log = logging.getLogger(__name__)


class RestServer:
    """REST server."""

    def __init__(self, app, host, port, api_key):
        """Init."""
        self.app = app
        self.host = host
        self.port = port
        self.api_key = api_key
        self.srv = None

        self.mapping = {
            'GET': {
                '/api/{api_key}': self._get_index,
                '/api/{api_key}/lights/{id}': self._get_light,
                '/api/{api_key}/lights/{id}/reinit': self._reinit_light,
                '/api/{api_key}/sensors/{id}': self._get_sensor,
            },

            'PUT': {
                '/api/{api_key}/config': self._put_config,
                '/api/{api_key}/lights/{id}': self._put_light,
            }
        }

        self.wapp = web.Application()
        for method, endpoints in self.mapping.items():
            for epname, epfun in endpoints.items():
                self.wapp.router.add_route(method, epname, self._rwrap(epfun))

    def _rwrap(self, handler_func):
        """Errors are handled and put in json format. """
        @functools.wraps(handler_func)
        async def wrapper(request):
            error_code = None
            try:
                result = await handler_func(request)
            except web.HTTPClientError as e:
                log.warning('Http error: %r %r', e.status_code, e.reason,
                            exc_info=True)
                error_code = e.status_code
                result = dict(error_code=error_code,
                              error_reason=e.reason,
                              status='FAILED')
            except Exception as e:
                log.warning('Server error', exc_info=True)
                error_code = 500
                result = dict(error_code=error_code,
                              error_reason='Unhandled exception',
                              status='FAILED')

            assert isinstance(result, dict)
            body = json.dumps(result).encode('utf-8')
            result = web.Response(body=body)
            result.headers['Content-Type'] = 'application/json'
            if error_code:
                result.set_status(error_code)
            return result

        return wrapper

    async def _get_index(self, request):
        log.info('Get config')
        return dict(answer=42)

    async def _reinit_light(self, request):
        log.info('Reinit light')
        print([hex(d.nwk) for d in self.app.devices.values()])
        try:
            light_id = int(request.match_info['id'], 16)
            try:
                light = await self.app.get_device(nwk=light_id)
            except KeyError:
                raise web.HTTPNotFound()
            await light.refresh_endpoints()
        except json.decoder.JSONDecodeError as err:
            log.info("Invalid json data")
        finally:
            pass
        return dict()

    async def _get_light(self, request):
        log.info('Get light')
        light_id = int(request.match_info['id'], 16)
        return dict(answer=42)

    async def _get_sensor(self, request):
        log.info('Get sensor')
        sensor_id = int(request.match_info['id'], 16)
        try:
            dev = await self.app.get_device(nwk=sensor_id)
            cluster = dev[1].on_off
            res = await cluster.bind()
            log.info('Bind response is: %r', res)
            res = await cluster.write_attributes({'cie_addr': dev.application.ieee})
            log.info('Attribute write response is: %r', res)
            res = await cluster.configure_reporting(0, 5 * 60, 6 * 60, '01')
            log.info('Configure reporting response is: %r', res)
        except KeyError:
            log.info(str([hex(d.nwk) for d in self.app.devices.values()]))
            raise web.HTTPNotFound()

        return dict(answer=42)

    async def _put_config(self, request):
        log.info('Put config')
        try:
            data = await request.json()
            if "permitjoin" in data:
                self.app.permit(int(data["permitjoin"]))
                log.info('Permitting join for %d seconds.', int(data["permitjoin"]))
        except json.decoder.JSONDecodeError as err:
            log.info("Invalid json data")
        finally:
            pass
        return dict()

    async def _put_light(self, request):
        log.info('Put light')
        try:
            light_id = int(request.match_info['id'], 16)
            try:
                dev = await self.app.get_device(nwk=light_id)
            except KeyError:
                log.info(str([hex(d.nwk) for d in self.app.devices.values()]))
                raise web.HTTPNotFound()
            data = await request.json()
            if "on" in data:
                if data["on"]:
                    log.info('Turn light on')
                    await dev[1].on_off.on()
                else:
                    log.info('Turn light off')
                    await dev[1].on_off.off()
            if "bri" in data and 0 <= data["bri"] <= 255:
                await dev[1].level.move_to_level_with_on_off(
                    data["bri"],
                    data["transitiontime"] if "transitiontime" in data else 0
                )
            if "ct" in data:
                await dev[1].light_color.move_to_color_temp(
                    data["ct"],
                    data["transitiontime"] if "transitiontime" in data else 0
                )
        except json.decoder.JSONDecodeError as err:
            log.info("Invalid json data")
        finally:
            pass
        return dict()

    async def start(self):
        """Start."""
        loop = asyncio.get_event_loop()
        self.srv = await loop.create_server(self.wapp.make_handler(),
                                                 self.host, self.port)

    def shutdown(self):
        """Shutdown."""
        self.srv.close()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.srv.wait_closed())


class WsConnection:
    """Single websocket connection."""

    def __init__(self, server, socket, path):
        self.server = server
        self.socket = socket
        self.path = path

    async def _handle_message(self, message):
        """Handle message."""
        pass

    async def handle(self):
        """Handle connection."""
        while True:
            message = await self.socket.recv()
            await self._handle_message(message)


class WsServer:
    """Websocket interface server."""

    def __init__(self, app, host, port):
        """Init."""
        self.app = app
        self.host = host
        self.port = port
        self.app.add_listener(self)
        self.srv = None
        self.connected = set()

    async def start(self):
        """Start."""
        self.srv = await websockets.serve(self._handler,
                                               self.host, self.port)

    async def broadcast(self, *args, **kwargs):
        """Write to all sockets."""
        for conn in self.connected:
            await conn.socket.send(*args, **kwargs)

    async def _handler(self, socket, path):
        """Handle connection."""
        conn = WsConnection(self, socket, path)
        self.connected.add(conn)
        try:
            await conn.handle()
        finally:
            self.connected.remove(conn)

    def shutdown(self):
        """Shutdown."""
        pass

    async def device_initialized(self, device):
        """Handle device initialized."""
        await self.broadcast("OK")


async def start(ctx):
    """Start websocket server."""
    ctx.obj['wsserver'] = WsServer(ctx.obj['app'],
                                   ctx.obj['wshost'],
                                   ctx.obj['wsport'])
    ctx.obj['restserver'] = RestServer(ctx.obj['app'],
                                       ctx.obj['resthost'],
                                       ctx.obj['restport'],
                                       ctx.obj['rest_api_key'])
    await ctx.obj['wsserver'].start()
    await ctx.obj['restserver'].start()
    await ctx.obj['app'].startup(auto_form=True)


def shutdown(ctx):
    """Shutdown servers."""
    ctx.obj['restserver'].shutdown()
    ctx.obj['wsserver'].shutdown()
