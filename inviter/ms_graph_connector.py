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
from typing import List, Iterable, Optional

from azure.identity import ClientSecretCredential
from msgraph.core import GraphClient

from inviter.config import Config
from inviter.room_structure import MXID, PowerLevel, RoomMember, Room, RoomAlias


async def get_client(tenant_id: str, client_id: str, client_secret: str) -> GraphClient:
    """

    :param tenant_id:
    :param client_id:
    :param client_secret:
    :return:
    """
    client_secret_credential = ClientSecretCredential(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    return GraphClient(credential=client_secret_credential)


async def api_get_group(group_id: str, client) -> dict:
    """Fetches information for a given group-id

    :param group_id: Group-id
    :param client: GraphClient
    :return: Group information (e.g. DisplayName)
    """
    return client.get('/groups/{group_id}'.format(group_id=group_id)).json()


async def api_get_group_owners(group_id: str, client) -> dict:
    """Fetches the owners of a group for a specified group-id

    :param group_id: Group-id
    :param client: GraphClient
    :return: Group owners
    """
    return client.get('/groups/{group_id}/owners'.format(group_id=group_id)).json().get('value')


def api_get_group_members_groups(group_id: str, client) -> Iterable:
    """Fetches the (non-transitive) members of a group for a specified group-id

    :param group_id: Group-id
    :param client: GraphClient
    :return: Group members
    """
    return client.get('/groups/{group_id}/members'.format(group_id=group_id)).json().get('value')


# Permissions needed: "Group.Read.All" and "User.Read.All"
def get_group_members_transitive(group_id: str, client) -> Iterable:
    """Fetches all transitive group-members of an Azure group

    :param group_id: Group-id
    :param client: GraphClient
    :return: Iterable of all transitive group-members
    """
    logging.getLogger("maubot").debug(f"Fetching transitiveMembers for group {group_id}")
    members = client.get('/groups/{group_id}/transitiveMembers'.format(group_id=group_id)).json().get('value')
    logging.getLogger("maubot").debug(f"get_group_members_transitive members: {members}")
    for member in members:
        if member.get('@odata.type') == '#microsoft.graph.user':
            yield member


async def get_group_roomalias(group_id: str, client: GraphClient) -> RoomAlias:
    """Converts an Azure group-id into the corresponding matrix room-alias

    :param group_id: Azure group-id
    :param client: GraphClient
    :return: Matrix RoomAlias (e.g. #room123:homerserverxy.com)
    """
    group_displayname = await api_get_group(group_id, client)
    group_displayname = group_displayname.get('displayName')
    logging.getLogger("maubot").debug(f"get_group_roomalias group_displayname: {group_displayname}")
    return RoomAlias.from_str(group_displayname.split(' ', 1)[1])


# group_id is top chat-user-group in Azure AD where all other MTRX-groups are in
async def get_rooms(config: Config) -> List[Room]:
    """Returns a list of rooms as defined in Azure AD.

    :param config: Bot configuration with azure credentials and configuration
    :return: A list of rooms with members and their respective roles
    """
    member_homeserver = config.get('mxid_homeserver', None)
    azure_config = config.get("azure_ad", None)
    group_id = azure_config.get('azure_root_group_id', None)
    tenant_id = azure_config.get('azure_tenant_id', None)
    client_id = azure_config.get('azure_client_id', None)
    client_secret = azure_config.get('azure_client_secret', None)
    client = await get_client(tenant_id, client_id, client_secret)
    if (not member_homeserver
            or not azure_config
            or not group_id
            or not tenant_id
            or not client_id
            or not client_secret):
        raise MSGraphConnectorMissingConfigException

    rooms = []
    # For each user group in Azure AD
    for mtrx_group in api_get_group_members_groups(group_id, client):
        logging.getLogger("maubot").debug(f"get_rooms: {mtrx_group}")
        mtrx_group_id = mtrx_group.get('id')

        # Get room members
        members = []
        for user in get_group_members_transitive(mtrx_group_id, client):
            # Only add room member if user is not disabled in AD
            account_enabled = await is_account_enabled(user.get('id'), client)
            if account_enabled is None:
                raise MSGraphConnectorException("Failed to fetch user data from graph api")
            elif account_enabled is False:
                continue

            # Get room member details from user
            mxid = config.get_renamed_mxid(
                MXID(user.get('userPrincipalName').split('@', 1)[0], member_homeserver))
            permission_level = PowerLevel.STANDARD

            # Group owners in AD get the moderator role in matrix room
            for owner in await api_get_group_owners(mtrx_group_id, client):
                if owner.get('userPrincipalName') == user.get('userPrincipalName'):
                    permission_level = PowerLevel.MODERATOR
            members.append(RoomMember(mxid, permission_level))

        # Add matrix room object with all room members and their roles
        rooms.append(Room(await get_group_roomalias(mtrx_group_id, client), members))
    return rooms


async def is_account_enabled(user_id: str, client: GraphClient) -> Optional[bool]:
    """Checks, if an account (user_id) is enabled in Azure AD

    :param user_id: User-id to check
    :param client: GraphClient
    :return: Whether account is enabled.
    """
    return client.get('/users/{user_id}?$select=DisplayName,accountEnabled'
                      .format(user_id=user_id)).json().get('accountEnabled')


class MSGraphConnectorException(Exception):
    pass


class MSGraphConnectorMissingConfigException(Exception):
    pass
