from socketio.defaultjson import default_json_dumps, default_json_loads

MSG_TYPES = {
    'open': 0,
    'close': 1,
    'ping': 2,
    'pong': 3,
    'message': 4,
    'upgrade': 5,
    'noop': 6,
    }

MSG_VALUES = dict((v, k) for k, v in MSG_TYPES.iteritems())

ERROR_REASONS = {
    'transport not supported': 0,
    'client not handshaken': 1,
    'unauthorized': 2
    }

REASONS_VALUES = dict((v, k) for k, v in ERROR_REASONS.iteritems())

ERROR_ADVICES = {
    'reconnect': 0,
    }

ADVICES_VALUES = dict((v, k) for k, v in ERROR_ADVICES.iteritems())

socketio_packet_attributes = ['type', 'name', 'data', 'endpoint', 'args',
                              'ackId', 'reason', 'advice', 'qs', 'id']


def encode(data, json_dumps=default_json_dumps):
    """
    Encode an attribute dict into a byte string.
    """
    payload = ''
    msg = str(MSG_TYPES[data['type']])
    if msg in ['0', '1']:
        # '1::' [path] [query]
        msg += '::' + data['endpoint']
        if 'qs' in data and data['qs'] != '':
            msg += ':' + data['qs']

    elif msg in ['2', '3']:
        # ping
        pass
        msg += data['data']

    elif msg in ['4']:
        payload = json_dumps(data['data'])
        # '3:' [id ('+')] ':' [endpoint] ':' [data]
        # '4:' [id ('+')] ':' [endpoint] ':' [json]
        # '5:' [id ('+')] ':' [endpoint] ':' [json encoded event]
        # The message id is an incremental integer, required for ACKs.
        # If the message id is followed by a +, the ACK is not handled by
        # socket.io, but by the user instead.
        pass
        msg += payload
    elif msg == '5':
        pass
    # NoOp, used to close a poll after the polling duration time
    elif msg == '6':
        pass

    return msg


def decode(rawstr, json_loads=default_json_loads):
    """
    Decode a rawstr packet arriving from the socket into a dict.
    """
    decoded_msg = {}

    msg_type = rawstr[0]
    msg_message = rawstr[1:]


    data = ''

    # common to every message
    msg_type_id = int(msg_type)
    if msg_type_id in MSG_VALUES:
        decoded_msg['type'] = MSG_VALUES[int(msg_type)]
    else:
        raise Exception("Unknown message type: %s" % msg_type)

    if msg_type == "0":  # open
        pass

    elif msg_type == "1":  # close
        decoded_msg['qs'] = data

    elif msg_type in ["2", "3"]:  # ping or pong
        decoded_msg['data'] = msg_message
    elif msg_type == "4":  # message
        decoded_msg['data'] = json_loads(data)

    elif msg_type == "5":  # upgrade
        pass
    elif msg_type == "6":  # noop
        pass

    return decoded_msg
