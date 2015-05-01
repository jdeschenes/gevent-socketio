import logging
log = logging.getLogger(__name__)
import sys
import re
import gevent
import urlparse

from gevent.pywsgi import WSGIHandler
from socketio import transports

class SocketIOHandler(WSGIHandler):
    RE_REQUEST_URL = re.compile(r"""
        ^/(?P<resource>.+?)
         /1
         /(?P<transport_id>[^/]+)
         /(?P<sessid>[^/]+)/?$
         """, re.X)
    RE_HANDSHAKE_URL = re.compile(r"^/(?P<resource>.+?)/1/$", re.X)
    # new socket.io versions (> 0.9.8) call an obscure url with two slashes
    # instead of a transport when disconnecting
    # https://github.com/LearnBoost/socket.io-client/blob/0.9.16/lib/socket.js#L361
    RE_DISCONNECT_URL = re.compile(r"""
        ^/(?P<resource>.+?)
         /(?P<protocol_version>[^/]+)
         //(?P<sessid>[^/]+)/?$
         """, re.X)

    handler_types = {
        'websocket': transports.WebsocketTransport,
        'flashsocket': transports.FlashSocketTransport,
        'htmlfile': transports.HTMLFileTransport,
        'xhr-multipart': transports.XHRMultipartTransport,
        'xhr-polling': transports.XHRPollingTransport,
        'jsonp-polling': transports.JSONPolling,
        'polling': transports.XHRPollingTransport
    }

    def __init__(self, config, *args, **kwargs):
        """Create a new SocketIOHandler.

        :param config: dict Configuration for timeouts and intervals
          that will go down to the other components, transports, etc..

        """
        self.socketio_connection = False
        self.allowed_paths = None
        self.config = config

        super(SocketIOHandler, self).__init__(*args, **kwargs)

        self.transports = self.handler_types.keys()
        if self.server.transports:
            self.transports = self.server.transports
            if not set(self.transports).issubset(set(self.handler_types)):
                raise ValueError("transports should be elements of: %s" %
                    (self.handler_types.keys()))

    def _do_handshake(self):
        socket = self.server.get_socket()
        dd = {'sid': str(socket.sessid),
              'upgrades': ['websocket'],
              'pingInterval': self.config['heartbeat_timeout']*1000,
              'pingTimeout': self.config['close_timeout']*1000
             }
        packet_to_send = '0{{"sid":"{0}","upgrades":["websocket"],"pingInterval":{1},"pingTimeout":{2}}}'.format(
            socket.sessid,
            self.config['heartbeat_timeout']*1000,
            self.config['close_timeout']*1000)
        length_of_packet = len(packet_to_send)
        first_unit = length_of_packet % 10
        second_unit = ((length_of_packet - first_unit)/10) % 10

        data = '\x00{0}{1}\xff0{{"sid":"{2}","upgrades":["websocket"],"pingInterval":{3},"pingTimeout":{4}}}'.format(
            chr(second_unit),
            chr(first_unit),
            socket.sessid,
            self.config['heartbeat_timeout']*1000,
            self.config['close_timeout']*1000)
        self.write_smart(data)

    def write_jsonp_result(self, data, wrapper="0"):
        self.start_response("200 OK", [
            ("Content-Type", "application/javascript"),
        ])
        self.result = ['io.j[%s]("%s");' % (wrapper, data)]

    def write_plain_result(self, data):
        socket = self.server.get_socket()

        self.start_response("200 OK", [
            ("Access-Control-Allow-Origin", self.environ.get('HTTP_ORIGIN', '*')),
#            ("Access-Control-Allow-Credentials", "true"),
#            ("Access-Control-Allow-Methods", "POST, GET, OPTIONS"),
#            ("Access-Control-Max-Age", 3600),
            ("Content-Type", "application/octet-stream"),
            ("Connection", "keep-alive"),
            ("Set-Cookie", "io={0}".format(socket.sessid))
#            ("Content-Type", "text/plain"),
        ])
        self.result = [data]

    def write_smart(self, data):
        args = urlparse.parse_qs(self.environ.get("QUERY_STRING"))

        if "jsonp" in args:
            self.write_jsonp_result(data, args["jsonp"][0])
        else:
            self.write_plain_result(data)

        self.process_result()

    def handle_one_response(self):
        """This function deals with *ONE INCOMING REQUEST* from the web.

        It will wire and exchange message to the queues for long-polling
        methods, otherwise, will stay alive for websockets.

        """
        path = self.environ.get('PATH_INFO')


        # Kick non-socket.io requests to our superclass
        if not path.lstrip('/').startswith(self.server.resource + '/'):
            return super(SocketIOHandler, self).handle_one_response()

        self.status = None
        self.headers_sent = False
        self.result = None
        self.response_length = 0
        self.response_use_chunked = False

        # This is analyzed for each and every HTTP requests involved
        # in the Socket.IO protocol, whether long-running or long-polling
        # (read: websocket or xhr-polling methods)
        request_method = self.environ.get("REQUEST_METHOD")
        request_tokens = self.RE_REQUEST_URL.match(path)
        handshake_tokens = self.RE_HANDSHAKE_URL.match(path)
        disconnect_tokens = self.RE_DISCONNECT_URL.match(path)

        args = urlparse.parse_qs(self.environ.get("QUERY_STRING"))

        if 'sid' not in args:
            # Deal with first handshake here, create the Socket and push
            # the config up.
            return self._do_handshake()
        else:
            session_id = args['sid'][0]
        if True:
            pass
        elif disconnect_tokens:
            # it's a disconnect request via XHR
            tokens = disconnect_tokens.groupdict()
        elif request_tokens:
            tokens = request_tokens.groupdict()
            # and continue...
        else:
            # This is no socket.io request. Let the WSGI app handle it.
            pass
        # Setup socket
        sessid = session_id
        socket = self.server.get_socket(sessid)
        if not socket:
            #TODO: BAD
            return self._do_handshake()
            self.handle_bad_request()
            return []  # Do not say the session is not found, just bad request
                       # so they don't start brute forcing to find open sessions

        if self.environ['QUERY_STRING'].startswith('disconnect'):
            # according to socket.io specs disconnect requests
            # have a `disconnect` query string
            # https://github.com/LearnBoost/socket.io-spec#forced-socket-disconnection
            socket.disconnect()
            self.handle_disconnect_request()
            return []
        transport_id = args["transport"][0]
        # Setup transport
        transport = self.handler_types.get(transport_id)

        # In case this is WebSocket request, switch to the WebSocketHandler
        # FIXME: fix this ugly class change
        old_class = None
        if issubclass(transport, (transports.WebsocketTransport,
                                  transports.FlashSocketTransport)):
            old_class = self.__class__
            self.__class__ = self.server.ws_handler_class
            self.prevent_wsgi_call = True  # thank you
            # TODO: any errors, treat them ??
            self.handle_one_response()  # does the Websocket dance before we continue

        # Make the socket object available for WSGI apps
        self.environ['socketio'] = socket

        # Create a transport and handle the request likewise
        self.transport = transport(self, self.config)

        # transports register their own spawn'd jobs now
        self.transport.do_exchange(socket, request_method)

        if not socket.connection_established:
            # This is executed only on the *first* packet of the establishment
            # of the virtual Socket connection.
            socket.connection_established = True
            socket.state = socket.STATE_CONNECTED
            socket._spawn_heartbeat()
            socket._spawn_watcher()

            try:
                # We'll run the WSGI app if it wasn't already done.
                if socket.wsgi_app_greenlet is None:
                    # TODO: why don't we spawn a call to handle_one_response here ?
                    #       why call directly the WSGI machinery ?
                    start_response = lambda status, headers, exc=None: None
                    socket.wsgi_app_greenlet = gevent.spawn(self.application,
                                                            self.environ,
                                                            start_response)
            except:
                self.handle_error(*sys.exc_info())

        # we need to keep the connection open if we are an open socket
        if transport_id in ['flashsocket', 'websocket']:
            # wait here for all jobs to finished, when they are done
            gevent.joinall(socket.jobs)

        # Switch back to the old class so references to this don't use the
        # incorrect class. Useful for debugging.
        if old_class:
            self.__class__ = old_class

        # Clean up circular references so they can be garbage collected.
        if hasattr(self, 'websocket') and self.websocket:
            if hasattr(self.websocket, 'environ'):
                del self.websocket.environ
            del self.websocket
        if self.environ:
            del self.environ

    def handle_bad_request(self):
        self.close_connection = True
        self.start_response("400 Bad Request", [
            ('Content-Type', 'text/plain'),
            ('Connection', 'close'),
            ('Content-Length', 0)
        ])


    def handle_disconnect_request(self):
        self.close_connection = True
        self.start_response("200 OK", [
            ('Content-Type', 'text/plain'),
            ('Connection', 'close'),
            ('Content-Length', 0)
        ])
