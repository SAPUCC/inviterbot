"""
Inviterbot - A maubot plugin to sync users from Azure AD and LDAP into matrix rooms
Copyright (C) 2022  SAP UCC Magdeburg

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
from typing import List, Dict

import mautrix
from maubot.matrix import MaubotMatrixClient
from mautrix.types import TextMessageEventContent, MessageType, Format, RoomAlias, RoomID, PowerLevelStateEventContent, \
    EventType
from mautrix.util import markdown

from inviter import ms_graph_connector, ldap_connector
from inviter.config import Config
from inviter.matrix_utils import MatrixUtils
from inviter.room_structure import Room


def generate_message_content(body: str) -> TextMessageEventContent:
    """Generate message content and format markdown

    :param body: Message string
    :return: Formatted content
    """
    message_html = markdown.render(
        body,
        allow_html=True
    )
    content = TextMessageEventContent(
        msgtype=MessageType.TEXT,
        format=Format.HTML,
        body=body,
        formatted_body=message_html
    )
    return content


async def get_room_struct_from_idp(config: Config) -> List[Room]:
    """Fetch rooms from IdP and return them as a list

    :param config: Maubot config
    :return: List of rooms from IdP
    """
    room_struct: List[Room] = None
    idp_type = config.get('idp_type', None)
    if not idp_type:
        logging.getLogger("maubot").error("idp_type is not set in configuration")
        raise MissingConfigException('The bot configuration is incomplete.')
    if idp_type == 'azure_ad':
        room_struct = await ms_graph_connector.get_rooms(config)
    elif idp_type == 'ldap':
        room_struct = ldap_connector.get_rooms(config)
    return room_struct


async def fetch_room_ids(client: MaubotMatrixClient, room_struct: List[Room]) -> List[Room]:
    """For every room in room_struct fetch room_id from alias and return populated room_struct

    :param client: Matrix client
    :param room_struct: List of rooms from IdP
    :return: List of rooms with populated ids
    """
    for room in room_struct:
        try:
            room.id = await client.resolve_room_alias(RoomAlias(str(room.alias)))
        except (mautrix.errors.MNotFound, mautrix.errors.MUnknown) as err:
            logging.getLogger("maubot").debug(f"room not found: {room.alias}")
            logging.getLogger("maubot").error(err)
    return room_struct


async def get_managed_rooms(client, config: Config, matrix_utils: MatrixUtils) -> List[Room]:
    """Returns list if all managed room objects and adds room-room_id

    A managed room is a room
     - which is in idp-structure
     - which exists in matrix
     - where bot is joined
     - where bot has sufficient power level
    :param matrix_utils:
    :param client: Matrix client
    :param config: Maubot config
    :return: List of rooms that are manageable
    """
    room_struct = await fetch_room_ids(client, await get_room_struct_from_idp(config))
    joined_rooms: List[RoomID] = await client.get_joined_rooms()
    managed_rooms = []
    for room in room_struct:
        if room.id and await matrix_utils.room_is_manageable(client, room.id.room_id, joined_rooms):
            managed_rooms.append(room)
    return managed_rooms


async def get_permissions_from_config(config: Config) -> PowerLevelStateEventContent:
    """Reads out the permission configuration and converts it into a PowerLevelStateEventContent.

    :param config: The config of the bot instance
    :return: A PowerLevelStateEventContent containing the configured permissions
    """
    permissions_dict = config["permissions"]
    users_default: int = permissions_dict["users_default"]
    events_default: int = permissions_dict["events_default"]
    state_default: int = permissions_dict["state_default"]
    invite: int = permissions_dict["invite"]
    kick: int = permissions_dict["kick"]
    ban: int = permissions_dict["ban"]
    redact: int = permissions_dict["redact"]

    events_dict: Dict[str, int] = permissions_dict["events"]

    result = PowerLevelStateEventContent(
        users_default=users_default,
        events_default=events_default,
        state_default=state_default,
        invite=invite,
        kick=kick,
        ban=ban,
        redact=redact
    )

    for event_type, power_level in events_dict.items():
        logging.getLogger("maubot").debug(event_type)
        result.events[EventType.find(event_type)] = power_level

    logging.getLogger("maubot").debug(f"PowerLevelStateEventContent from config: {result}")

    return result


class MissingConfigException(Exception):
    pass
