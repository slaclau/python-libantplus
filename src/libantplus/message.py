"""Provide a class structure for ANT+ messages."""

__version__ = "2023-04-16"
# 2023-04-16    Rewritten in class based fashion

import binascii
import struct
from typing import Dict

from enum import Enum

import libantplus.structConstants as sc
from libantplus.plus.page import AntPage
from numpy import byte


class Id(Enum):
    """Message ID enum."""

    RF_EVENT = 0x01

    ANTversion = 0x3E
    BroadcastData = 0x4E
    AcknowledgedData = 0x4F
    ChannelResponse = 0x40
    Capabilities = 0x54

    UnassignChannel = 0x41
    AssignChannel = 0x42
    ChannelPeriod = 0x43
    ChannelSearchTimeout = 0x44
    ChannelRfFrequency = 0x45
    SetNetworkKey = 0x46
    ResetSystem = 0x4A
    OpenChannel = 0x4B
    CloseChannel = 0x4C
    RequestMessage = 0x4D

    ChannelID = 0x51
    ChannelStatus = 0x52
    ChannelTransmitPower = 0x60

    EnableExtendedMessages = 0x66
    LibConfig = 0x6E

    StartUp = 0x6F

    BurstData = 0x50

    OpenRxScan = 0x5B


# Manufacturer ID       see FitSDKRelease_21.20.00 profile.xlsx
Manufacturer_garmin = 1
Manufacturer_dynastream = 15
Manufacturer_tacx = 89
Manufacturer_trainer_road = 281
Manufacturer_dev = 255

SYNC = 0xA4


class AntMessage(bytes):
    """A message to be sent over an ANT+ interface."""

    types: Dict[Id, "type[AntMessage]"] = {}

    def __init__(self, data: bytes):
        super(bytes, data)

    @classmethod
    def compose(cls, message_id: Id, info: AntPage) -> "AntMessage":
        """Compose a message from its id and contents."""
        fSynch = sc.unsigned_char
        fLength = sc.unsigned_char
        fId = sc.unsigned_char
        fInfo = str(len(info)) + sc.char_array

        message_format = sc.no_alignment + fSynch + fLength + fId + fInfo
        data = struct.pack(message_format, SYNC, len(info), message_id.value, info)

        data += calc_checksum(data)

        return AntMessage(data)

    @classmethod
    def decompose(cls, message) -> tuple:
        """Decompose a message into its constituent parts."""
        sync = 0
        length = 0
        messageID = None
        checksum = 0
        info = binascii.unhexlify("")  # NULL-string bytes
        rest = ""  # No remainder (normal)
        burst_sequence_number = None
        flag = 0
        extended_data = b""

        try:
            assert message[0] == SYNC
            sync = message[0]
            assert len(message) > 1
            length = message[1]
            assert len(message) == length + 4
        except AssertionError:
            print(length)
            print(len(message))
            raise InvalidMessageError from None

        if len(message) > 2:
            messageID = Id(message[2])
        if len(message) > 3 + length:
            if length:
                info = message[3 : 3 + length]  # Info, if length > 0
            checksum = message[3 + length]  # Character after info
            assert checksum == int.from_bytes(calc_checksum(message))
        if len(message) > 4 + length:
            rest = message[4 + length :]  # Remainder (should not occur)

        Channel = -1
        DataPageNumber = -1
        if length >= 1:
            Channel = message[3]
        if length >= 2:
            DataPageNumber = message[4]

        if messageID == Id.BurstData:
            burst_sequence_number = (
                Channel & 0b11100000
            ) >> 5  # Upper 3 bits # noqa: F841
            Channel = Channel & 0b00011111  # Lower 5 bits

        if messageID in (Id.BroadcastData, Id.AcknowledgedData, Id.BurstData):
            if length > 9:
                flag = info[9]
                extended_data = info[10:length]
                info = info[0:9]

        return (
            sync,
            length,
            messageID,
            info,
            checksum,
            rest,
            Channel,
            DataPageNumber,
            burst_sequence_number,
            flag,
            extended_data,
        )

    @staticmethod
    def _decompose_extended_data(flag, extended_data) -> dict:
        rtn = {}
        count = 0
        if (flag & 0x80) == 0x80:
            rtn["channel_id"] = {}
            rtn["channel_id"]["device_number"] = int.from_bytes(
                extended_data[count : count + 2], byteorder="little"
            )
            rtn["channel_id"]["device_type_id"] = extended_data[count + 2]
            rtn["channel_id"]["transmission_type"] = extended_data[count + 3]
            count += 4
        if (flag & 0x40) == 0x40:
            rtn["rssi"] = {}
            rtn["rssi"]["type"] = hex(extended_data[count])
            if rtn["rssi"]["type"] == 0x20:
                rtn["rssi"]["value"] = int.from_bytes(
                    extended_data[count + 1].to_bytes(), signed=True
                )
                rtn["rssi"]["threshold"] = int.from_bytes(
                    extended_data[count + 2].to_bytes(), signed=True
                )
            elif rtn["rssi"]["type"] == 0x10:
                rtn["rssi"]["acg"] = int.from_bytes(
                    extended_data[count + 1 : count + 2].to_bytes(), signed=True
                )
                rtn["rssi"]["threshold"] = int.from_bytes(
                    extended_data[count + 2].to_bytes(), signed=True
                )
            count += 3
            if rtn["rssi"]["type"] == 0x10:
                count += 1
        if (flag & 0x20) == 0x20:
            rtn["timestamp"] = int.from_bytes(
                extended_data[count : count + 2], byteorder="little"
            )
            count += 2

        return rtn

    @classmethod
    def decompose_to_dict(cls, message) -> dict:
        """Decompose message into dictionary."""
        rtn = {}
        response = cls.decompose(message)
        rtn["sync"] = response[0]
        rtn["length"] = response[1]
        rtn["id"] = response[2]
        rtn["info"] = response[3]
        rtn["checksum"] = response[4]
        rtn["rest"] = response[5]
        rtn["channel"] = response[6]
        rtn["page_number"] = response[7]
        if rtn["id"] == Id.BurstData:
            rtn["sequence_number"] = response[8]
        if rtn["id"] in (Id.BroadcastData, Id.AcknowledgedData, Id.BurstData):
            if response[9] != 0:
                rtn["flag"] = response[9]
                rtn["extended_data"] = response[10]
                rtn["parsed_extended_data"] = cls._decompose_extended_data(
                    response[9], response[10]
                )

        return rtn

    @classmethod
    def type_from_id(cls, message_id: Id):
        """Return message class for given Id."""
        if not cls.types:
            first_subclasses = AntMessage.__subclasses__()
            subclasses = []
            for subclass in first_subclasses:
                subclasses += subclass.__subclasses__()

            for subclass in subclasses:
                if hasattr(subclass, "message_id"):
                    cls.types[subclass.message_id] = subclass

        try:
            return cls.types[message_id]
        except KeyError:
            return None


def calc_checksum(message):
    """Calculate checksum."""
    xor_value = 0
    length = message[1]  # byte 1; length of info
    length += 3  # Add synch, len, id
    for i in range(0, length):  # Process bytes as defined in length
        xor_value = xor_value ^ message[i]

    #   print('checksum', logfile.HexSpace(message), xor_value, bytes([xor_value]))

    return bytes([xor_value])


class SpecialMessageSend(AntMessage):
    """Special case messages - send."""

    message_id: Id
    message_format: str
    info: bytes

    def __new__(cls, **kwargs):
        """Return result of :meth:`create`.

        This allows :meth:`__init__` to be used.
        """
        return cls.create(**kwargs)

    @classmethod
    def _parse_args(cls, **kwargs) -> bytes:
        raise NotImplementedError

    @classmethod
    def create(cls, **kwargs):
        """Create message."""
        info = cls._parse_args(**kwargs)
        return cls.compose(cls.message_id, info)


class SpecialMessageReceive(AntMessage):
    """Special case messages - receive."""

    message_id: Id
    message_format: str
    info: bytes

    @classmethod
    def to_dict(cls, message):
        """Convert message to dict."""
        raise NotImplementedError

    @classmethod
    def _get_content(cls, message):
        info = cls._get_info(message)
        return struct.unpack(cls.message_format, info)

    @classmethod
    def _get_info(cls, message):
        message_id = cls.decompose_to_dict(message)["id"]
        if message_id != cls.message_id:
            raise WrongMessageId(message_id, cls.message_id)
        return cls.decompose_to_dict(message)["info"]


class OpenRxScanMessage(SpecialMessageSend):
    """Open channel 0 in continuous scanning mode."""

    message_id = Id.OpenRxScan
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        return struct.pack(cls.message_format, 0, 0)


class EnableExtendedMessagesMessage(SpecialMessageSend):
    """Enable extended messages."""

    message_id = Id.EnableExtendedMessages
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs) -> bytes:
        enable = int(kwargs["enable"])
        return struct.pack(cls.message_format, 0, enable)


class LibConfigMessage(SpecialMessageSend):
    """Configure extended messages."""

    message_id = Id.LibConfig
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs) -> bytes:
        timestamp = kwargs["timestamp"] if "timestamp" in kwargs else False
        rssi = kwargs["rssi"] if "rssi" in kwargs else False
        channel_id = kwargs["channel_id"] if "channel_id" in kwargs else False

        return struct.pack(
            cls.message_format, 0, timestamp * 0x20 + rssi * 0x40 + channel_id * 0x80
        )


class UnassignChannelMessage(SpecialMessageSend):
    """Unassign channel."""

    message_id = Id.UnassignChannel
    message_format = sc.no_alignment + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        return struct.pack(cls.message_format, channel)


class AssignChannelMessage(SpecialMessageSend):
    """Assign channel."""

    message_id = Id.AssignChannel
    message_format = (
        sc.no_alignment + sc.unsigned_char + sc.unsigned_char + sc.unsigned_char
    )

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        channel_type = kwargs["type"].value
        network = kwargs["network"]
        return struct.pack(cls.message_format, channel, channel_type, network)


class SetChannelPeriodMessage(SpecialMessageSend):
    """Set period."""

    message_id = Id.ChannelPeriod
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_short

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        period = kwargs["period"]
        return struct.pack(cls.message_format, channel, period)


class SetChannelSearchTimeoutMessage(SpecialMessageSend):
    """Set search timeout."""

    message_id = Id.ChannelSearchTimeout
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_short

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        timeout = kwargs["timeout"]
        return struct.pack(cls.message_format, channel, timeout)


class SetChannelFrequencyMessage(SpecialMessageSend):
    """Set channel RF frequency."""

    message_id = Id.ChannelRfFrequency
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        frequency = kwargs["frequency"]
        return struct.pack(cls.message_format, channel, frequency)


class SetNetworkKeyMessage(SpecialMessageSend):
    """Set network key."""

    message_id = Id.SetNetworkKey
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_long_long

    @classmethod
    def _parse_args(cls, **kwargs):
        network = kwargs["network"] if "network" in kwargs else 0x00
        key = kwargs["key"] if "key" in kwargs else 0x45C372BDFB21A5B9
        return struct.pack(cls.message_format, network, key)


class ResetSystemMessage(SpecialMessageSend):
    """Reset system."""

    message_id = Id.ResetSystem
    message_format = sc.no_alignment + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        return struct.pack(cls.message_format, 0x00)


class OpenChannelMessage(SpecialMessageSend):
    """Open channel."""

    message_id = Id.OpenChannel
    message_format = sc.no_alignment + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        return struct.pack(cls.message_format, channel)


class CloseChannelMessage(SpecialMessageSend):
    """Open channel."""

    message_id = Id.CloseChannel
    message_format = sc.no_alignment + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        return struct.pack(cls.message_format, channel)


class RequestMessage(SpecialMessageSend):
    """Request message."""

    message_id = Id.RequestMessage
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"] if "channel" in kwargs else 0
        requested_id = kwargs["id"].value
        return struct.pack(cls.message_format, channel, requested_id)


class SetChannelIdMessage(SpecialMessageSend, SpecialMessageReceive):
    """Set channel ID."""

    message_id = Id.ChannelID
    message_format = (
        sc.no_alignment
        + sc.unsigned_char
        + sc.unsigned_short
        + sc.unsigned_char
        + sc.unsigned_char
    )

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        device_number = kwargs["device_number"]
        device_type_id = kwargs["device_type_id"]
        transmission_type = int(kwargs["type"])
        return struct.pack(
            cls.message_format,
            channel,
            device_number,
            device_type_id,
            transmission_type,
        )

    @classmethod
    def to_dict(cls, message):
        """Return channel id."""
        info = cls._get_info(message)
        rtn = {}
        rtn["channel"] = info[0]
        rtn["device_number"] = int.from_bytes(info[1:3], byteorder="little")
        rtn["device_type_id"] = info[3]
        rtn["transmission_type"] = info[4]

        return rtn


class SetChannelTransmitPowerMessage(SpecialMessageSend):
    """Set transmit power."""

    message_id = Id.ChannelTransmitPower
    message_format = sc.no_alignment + sc.unsigned_char + sc.unsigned_char

    @classmethod
    def _parse_args(cls, **kwargs):
        channel = kwargs["channel"]
        power = kwargs["power"]
        return struct.pack(cls.message_format, channel, power)


class ChannelResponseMessage(SpecialMessageReceive):
    """Sent by the dongle in response to channel events."""

    message_id = Id.ChannelResponse
    message_format = (
        sc.no_alignment + sc.unsigned_char + sc.unsigned_char + sc.unsigned_char
    )

    @classmethod
    def to_dict(cls, message):
        """Return breakdown of channel response message."""
        info = cls._get_info(message)
        rtn = {}
        rtn["channel"] = info[0]
        rtn["id"] = Id(info[1])
        rtn["code"] = cls.Code(info[2])

        return rtn

    class Code(Enum):
        """Response codes enum."""

        RESPONSE_NO_ERROR = 0
        EVENT_RX_SEARCH_TIMEOUT = 1
        EVENT_RX_FAIL = 2
        EVENT_TX = 3
        EVENT_TRANSFER_RX_FAILED = 4
        EVENT_TRANSFER_TX_COMPLETED = 5
        EVENT_TRANSFER_TX_FAILED = 6
        EVENT_CHANNEL_CLOSED = 7
        EVENT_RX_FAIL_GO_TO_SEARCH = 8
        EVENT_CHANNEL_COLLISION = 9
        EVENT_TRANSFER_TX_START = 10
        EVENT_TRANSFER_NEXT_DATA_BLOCK = 17
        CHANNEL_IN_WRONG_STATE = 21
        CHANNEL_NOT_OPENED = 22
        CHANNEL_ID_NOT_SET = 24
        CLOSE_ALL_CHANNELS = 25
        TRANSFER_IN_PROGRESS = 31
        TRANSFER_SEQUENCE_NUMBER_ERROR = 32
        TRANSFER_IN_ERROR = 33
        MESSAGE_SIZE_EXCEEDS_LIMIT = 39
        INVALID_MESSAGE = 40
        INVALID_NETWORK_NUMBER = 41
        INVALID_LIST_ID = 48
        INVALID_SCAN_TX_CHANNEL = 49
        INVALID_PARAMETER_PROVIDED = 51
        EVENT_SERIAL_QUE_OVERFLOW = 52
        EVENT_QUE_OVERFLOW = 53
        ENCRYPT_NEGOTIATION_SUCCESS = 56
        ENCRYPT_NEGOTIATION_FAIL = 57
        NVM_FULL_ERROR = 64
        NVM_WRITE_ERROR = 65
        USB_STRING_WRITE_FAIL = 112
        MESG_SERIAL_ERROR_ID = 174


class StartupMessage(SpecialMessageReceive):
    """Sent by dongle on startup."""

    message_id = Id.StartUp
    message_format = sc.no_alignment + sc.unsigned_char

    @classmethod
    def to_dict(cls, message):
        """Return bit field of startup reason."""
        info = cls._get_info(message)
        rtn = {}
        bits = bin(info[0])[2:]
        bits = "0" * (8 - len(bits)) + bits
        rtn["bits"] = bits

        if info[0] == 0:
            reset_type = "POWER_ON_RESET"
        elif bits[7 - 5] == "1":
            reset_type = "COMMAND_RESET"
        else:
            reset_type = ""

        rtn["type"] = reset_type if reset_type != "" else bits
        return rtn


class CapabilitiesMessage(SpecialMessageReceive):
    """Sent by dongle with capabilities."""

    message_id = Id.Capabilities

    @classmethod
    def to_dict(cls, message) -> dict:
        """Return max channels and networks."""
        info = cls._get_info(message)
        length = cls.decompose_to_dict(message)["length"]
        rtn = {}
        if length > 0:
            rtn["max_channels"] = info[0]
        if length > 1:
            rtn["max_networks"] = info[1]
        if length > 2:
            bits = bin(info[2])[2:]
            bits = "0" * (8 - len(bits)) + bits
            rtn["standard_options"] = bits
            rtn["standard_options_parsed"] = cls._parse_standard_options(bits)
        if length > 3:
            bits = bin(info[3])[2:]
            bits = "0" * (8 - len(bits)) + bits
            rtn["advanced_options"] = bits
            rtn["advanced_options_parsed"] = cls._parse_advanced_options(bits)
        if length > 4:
            bits = bin(info[4])[2:]
            bits = "0" * (8 - len(bits)) + bits
            rtn["advanced_options2"] = bits
            rtn["advanced_options2_parsed"] = cls._parse_advanced_options2(bits)
        if length > 5:
            rtn["max_sensrcore_channels"] = info[5]

        return rtn

    @staticmethod
    def _parse_standard_options(bits):
        rtn = {}
        rtn["CAPABILITIES_NO_RECEIVE_CHANNELS"] = bits[7]
        rtn["CAPABILITIES_NO_TRANSMIT_CHANNELS"] = bits[6]
        rtn["CAPABILITIES_NO_RECEIVE_MESSAGES"] = bits[5]
        rtn["CAPABILITIES_NO_TRANSMIT_MESSAGES"] = bits[4]
        rtn["CAPABILITIES_NO_ACKD_MESSAGES"] = bits[3]
        rtn["CAPABILITIES_NO_BURST_MESSAGES"] = bits[2]

        return rtn

    @staticmethod
    def _parse_advanced_options(bits):
        rtn = {}
        rtn["CAPABILITIES_NETWORK_ENABLED"] = bits[6]
        rtn["CAPABILITIES_SERIAL_NUMBER_ENABLED"] = bits[4]
        rtn["CAPABILITIES_PER_CHANNEL_TX_POWER_ENABLED"] = bits[3]
        rtn["CAPABILITIES_LOW_PRIORITY_SEARCH_ENABLED"] = bits[3]
        rtn["CAPABILITIES_SCRIPT_ENABLED"] = bits[1]
        rtn["CAPABILITIES_SEARCH_LIST_ENABLED"] = bits[0]

        return rtn

    @staticmethod
    def _parse_advanced_options2(bits):
        rtn = {}
        rtn["CAPABILITIES_LED_ENABLED"] = bits[7]
        rtn["CAPABILITIES_EXT_MESSAGE_ENABLED"] = bits[6]
        rtn["CAPABILITIES_SCAN_MODE_ENABLED"] = bits[5]
        rtn["CAPABILITIES_PROX_SEARCH_ENABLED"] = bits[3]
        rtn["CAPABILITIES_EXT_ASSIGN_ENABLED"] = bits[2]
        rtn["CAPABILITIES_FS_ANTFS_ENABLED"] = bits[1]
        rtn["CAPABILITIES_FIT1_ENABLED"] = bits[0]

        return rtn


class VersionMessage(SpecialMessageReceive):
    """Sent by dongle with capabilities."""

    message_id = Id.ANTversion

    @classmethod
    def to_dict(cls, message) -> dict:
        """Return version."""
        info = cls._get_info(message)
        version = bytes(info[0:-1]).decode("utf-8")
        return {"version": version}


class InvalidMessageError(Exception):
    """Raise when trying to decompose an invalid messsage."""


class WrongMessageId(Exception):
    """Raise when trying to parse the wrong type of message."""

    def __init__(self, received, expected):
        self.received = received
        self.expected = expected
        self.message = f"Received {received} and expected {expected}."
