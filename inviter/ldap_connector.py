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
from typing import List, Optional, Dict, Tuple

from ldap3 import Server, Connection, ALL

from .config import Config
from .enum import PowerLevel
from .room_structure import Room, RoomAlias, RoomMember, MXID


def get_connection(host: str, port: int, bind_dn: str, bind_pass: str) -> Connection:
    """Return an LDAP connection with bind.

    :param host: LDAP host
    :param port: LDAP port
    :param bind_dn: LDAP bind DN
    :param bind_pass: LDAP bind password
    :return: Connection with bind
    """
    server = Server(host=host, port=port, get_info=ALL)
    connection = Connection(server, user=bind_dn, password=bind_pass)

    if not connection.bind():
        logging.getLogger("maubot").error(f"Error in bind: {connection.result}")
        raise LDAPConnectorException("Error in bind")
    return connection


def get_rooms(config: Config) -> Optional[List[Room]]:
    """Returns a list of rooms as defined in LDAP.

    :param config: Bot configuration with LDAP credentials and configuration
    :return: A list of rooms with members and their respective roles
    """
    homeserver = config.get("mxid_homeserver", None)
    ldap_config = config.get("ldap", None)
    base_dn_groups = ldap_config.get("base_dn_groups", None)
    base_dn_users = ldap_config.get("base_dn_users", None)
    user_filter = ldap_config.get("user_filter", None)
    username_attribute = ldap_config.get("username_attribute", "uid")
    if (not homeserver
            or not ldap_config
            or not base_dn_groups
            or not base_dn_users
            or not user_filter):
        raise LDAPConnectorMissingConfigException

    connection = get_connection(host=ldap_config.get("host"),
                       port=ldap_config.get("port"),
                       bind_dn=ldap_config.get("bind_dn"),
                       bind_pass=ldap_config.get("bind_password"))

    if not connection.search(base_dn_groups, "(&(objectClass=group)(cn=xxx*))", attributes=["cn"]):
        logging.getLogger("maubot").warning(f"LDAP search not successful: {connection.result}")
        return None

    response: List[dict] = connection.response
    groups: Dict[str, str] = {}
    owner_groups: Dict[str, str] = {}
    for element in response:
        dn: str = element.get('dn')

        # replace first xxx with '#' and second xxx with ':'
        room_alias: str = dn.split(',', 1)[0].replace('cn=', '').replace('xxx', '#', 1).replace('xxx', ':', 1)

        # Set as owners if '_owners' is part of the user-group name
        owners = False
        if '_owners' in room_alias:
            owners = True
            room_alias = room_alias.replace('_owners', '')
        if owners:
            owner_groups[room_alias] = dn
        else:
            groups[room_alias] = dn

    rooms: List[Room] = []
    for group in groups:
        group_members = get_group_members(connection, groups.get(group), base_dn_users, username_attribute, user_filter)
        group_owners = get_group_members(connection, owner_groups.get(group), base_dn_users, username_attribute, user_filter) if group in owner_groups else []

        room_members: List[RoomMember] = []
        for group_member in group_members:
            if is_account_enabled(group_member):
                power_level = PowerLevel.MODERATOR if group_member in group_owners else PowerLevel.STANDARD
                room_members.append(RoomMember(
                    config.get_renamed_mxid(MXID(group_member[0], homeserver)), power_level)
                )

        rooms.append(Room(RoomAlias.from_str(group), room_members))

    return rooms


def get_group_members(connection: Connection, group_dn: str, user_dn: str, username_attribute: str, search_filter: str = '(objectClass=*)')\
        -> List[Tuple[str, bool]]:
    """Return members of a given user-group.

    :param connection: Connection with bind
    :param group_dn: DN of user-group in which the members should be
    :param user_dn: Base DN of all users
    :param search_filter: An optional search filter
    :return: Tuple list including user-id and nsAccountLock state boolean
    """
    members: List[Tuple[str, bool]] = []
    search = f"(&(memberOf={group_dn}){search_filter})"
    connection.search(user_dn, search, attributes=[username_attribute, "nsAccountLock"])
    entries = connection.entries
    for element in entries:
        members.append((str(getattr(element, username_attribute)), element.nsAccountLock.value))
    return members


def is_account_enabled(group_member: Tuple[str, bool]) -> Optional[bool]:
    """Dumb wrapper function returning if a user is enabled or not.

    :param group_member: A user tuple with user-id and nsAccountLock boolean
    :return: Boolean whether account is enabled
    """
    return not group_member[1]


class LDAPConnectorException(Exception):
    pass


class LDAPConnectorMissingConfigException(Exception):
    pass
