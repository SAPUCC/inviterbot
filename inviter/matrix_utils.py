"""
Inviterbot - A maubot plugin to sync users from Azure AD and LDAP into matrix rooms
Copyright (C) 2022  SAP UCC Magdeburg

Parts of this code are taken from https://github.com/davidmehren/maubot-ldap-inviter/blob/main/inviter/matrix_utils.py
which was released 2021 by David Mehren under MIT license.

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
import asyncio
from typing import List, Tuple

import mautrix
from maubot.matrix import MaubotMatrixClient
from mautrix.client.api.events import EventMethods
from mautrix.client.api.rooms import RoomMethods
from mautrix.errors import MNotFound
from mautrix.types import (
    RoomID,
    RoomDirectoryVisibility,
    EventType,
    RoomNameStateEventContent,
    UserID,
    PowerLevelStateEventContent,
    StateEvent,
    Membership, RoomEncryptionStateEventContent, EncryptionAlgorithm, StateEventContent, RoomAlias,
    CanonicalAliasStateEventContent,
)
from mautrix.util.logging import TraceLogger

from .enum import PowerLevel
from .room_structure import MXID, UserInfo, UserInfoMap
from .room_structure import RoomAlias as StructRoomAlias


def permission_diff(current_permissions: StateEventContent, new_permissions: StateEventContent) -> bool:
    """Static function to return whether permissions have changed inside a PowerLevelStateEventContent

    :param current_permissions: Current PowerLevelStateEventContent
    :param new_permissions: New PowerLevelStateEventContent
    :return: True if changes are detected
    """
    users_default_diff: bool = current_permissions.get('users_default') != new_permissions.get('users_default')
    events_default_diff: bool = current_permissions.get('events_default') != new_permissions.get('events_default')
    state_default_diff: bool = current_permissions.get('state_default') != new_permissions.get('state_default')
    invite_diff: bool = current_permissions.get('invite') != new_permissions.get('invite')
    kick_diff: bool = current_permissions.get('kick') != new_permissions.get('kick')
    ban_diff: bool = current_permissions.get('ban') != new_permissions.get('ban')
    redact_diff: bool = current_permissions.get('redact') != new_permissions.get('redact')

    cur_per_events = current_permissions.get('events')
    new_per_events = new_permissions.get('events')

    events_diff: bool = False
    for event in new_per_events:
        events_diff = True if cur_per_events.get(event) != new_per_events.get(event) else events_diff

    return (users_default_diff
            or events_default_diff
            or state_default_diff
            or invite_diff
            or kick_diff
            or ban_diff
            or redact_diff
            or events_diff)


class MatrixUtils:
    client: MaubotMatrixClient = None
    room_methods: RoomMethods = None
    event_methods: EventMethods = None
    logger = None

    def __init__(self, client: MaubotMatrixClient, log: TraceLogger):
        self.client = client
        self.room_methods = RoomMethods(api=client.api)
        self.event_methods = EventMethods(api=client.api)
        self.logger = log

    async def ensure_room_visibility(self, room_id: RoomID, visibility: str) -> None:
        """Ensures that a room has the specified visibility

        :param room_id: room id
        :param visibility: Visibility which the room should have
        :return: Nothing
        """
        # self.logger.debug(f"Ensuring visibility for {room_id}...")
        current_visibility = await self.room_methods.get_room_directory_visibility(
            room_id
        )
        if current_visibility != visibility:
            await self.room_methods.set_room_directory_visibility(
                room_id, RoomDirectoryVisibility(visibility)
            )
            self.logger.debug(f"Changed visibility of {room_id} from {current_visibility} to {visibility}.")

    async def ensure_room_name(self, room_id: RoomID, name: str) -> None:
        """Ensures that a room has the specified name

        :param room_id: Room id
        :param name: Name which the room should have
        :return: Nothing
        """
        try:
            current_name = (
                await self.room_methods.get_state_event(room_id, EventType.ROOM_NAME)
            )["name"]
        except MNotFound:
            current_name = ""
        if not current_name == name:
            self.logger.debug(f"Setting name '{name}' for room {room_id}")
            await self.event_methods.send_state_event(
                room_id, EventType.ROOM_NAME, RoomNameStateEventContent(name)
            )

    async def create_room_with_alias(self, alias: str, encryption: bool) -> RoomID:
        """Creates a room with given alias and enables encryption.

        :param encryption: Whether room should be created with encryption enabled.
        :param alias: Room alias
        :return: Room id of newly created room
        """
        self.logger.debug(f"Creating room {alias}...")

        # Mitigate synapse rate-limit
        await asyncio.sleep(0.5)

        alias_local_part = alias[1:-1].split(":")[0]
        new_room_id = await self.room_methods.create_room(alias_localpart=alias_local_part)
        self.logger.debug(f"Created room: {new_room_id} for {alias}")
        if encryption:
            await self.room_methods.send_state_event(
                new_room_id,
                EventType.ROOM_ENCRYPTION,
                RoomEncryptionStateEventContent(EncryptionAlgorithm.MEGOLM_V1)
            )
            self.logger.debug(f"Enabled encryption for new room.")
        return new_room_id

    async def ensure_room_with_alias(self, alias: str, encryption_on_room_creation: bool,
                                     may_create_room: bool) -> RoomID:
        """Find the room-id of a given alias.
        If room is not present, create one. If room already there, raise exception.

        :param alias: Room alias
        :param encryption_on_room_creation: Whether room should be created with encryption enabled if not present.
        :param may_create_room: Whether a new room may be created if the room is not found.
        :return: Room id
        """
        # self.logger.debug(f"Ensuring {alias} exists...")
        try:
            room = await self.room_methods.resolve_room_alias(RoomAlias(alias))
        except MNotFound:
            self.logger.debug(f"Alias {alias} not found.")
            if may_create_room:
                return await self.create_room_with_alias(alias, encryption_on_room_creation)
        if room is None:
            raise EnsureRoomWithAliasException(f"Could not find nor create room for alias {alias}")
        else:
            # self.logger.debug(f"Room {alias} already exists: {room.room_id}")
            return room.room_id

    @staticmethod
    def state_events_to_member_list(state_events: [StateEvent]) -> (List[str], List[str]):
        """Convert a list of state-events into member-lists

        :param state_events:
        :return: Lists of members and invitees gathered from the state_events
        """
        member_mxids = []
        invite_mxids = []
        member_event_type = EventType.find("m.room.member", EventType.Class.STATE)
        for event in state_events:
            if (
                    event.type == member_event_type
                    and event.content.membership == Membership.JOIN
            ):
                member_mxids.append(event.state_key)
            if (
                    event.type == member_event_type
                    and event.content.membership == Membership.INVITE
            ):
                invite_mxids.append(event.state_key)
        return member_mxids, invite_mxids

    async def ensure_room_membership(self, room_id: RoomID, user_info_map: UserInfoMap,
                                     inviting: bool = True, kicking: bool = False) -> Tuple[List[str], List[str]]:
        """Ensures that given users are member of a room and that other users from the own homeserver
        are kicked, if they should not be in the room

        :param inviting: If users may be invited
        :param kicking: If users may be kicked
        :param room_id: Room id
        :param user_info_map: Map of users and their permissions, only used as user-list here
        :return: Nothing
        """
        room_member_events = await self.event_methods.get_members(room_id)
        room_members, room_invitees = self.state_events_to_member_list(
            room_member_events
        )
        # self.logger.debug(f"Room {room_id} has members:{str(room_members)}")
        # self.logger.debug(f"Room {room_id} has invitees:{str(room_invitees)}")

        # Invite users if not in room
        invited_users: [str] = []
        for mxid in user_info_map:
            if mxid not in room_members and mxid not in room_invitees:
                self.logger.debug(f"User {mxid} not invited or member in the room, inviting...")
                if inviting:
                    # Mitigate synapse rate-limit (per room and second: 0.3 allowed)
                    await asyncio.sleep(3.4)
                    await self.room_methods.invite_user(room_id, mxid)
                invited_users.append(mxid)
        # self.logger.debug(f"Successfully ensured invitees for {room_id}")

        # Kick users of own homeserver if not in user_info_map
        kicked_users: [str] = []
        invited_or_member = room_members + room_invitees
        power_level_state = await self.room_methods.get_state_event(
            room_id, EventType.ROOM_POWER_LEVELS
        )
        for mxid in invited_or_member:
            if mxid not in user_info_map and await self.user_is_kickable(room_id, UserID(mxid), power_level_state):
                self.logger.debug(f"User {mxid} should not be in this room, kicking...")
                if kicking:
                    # Mitigate synapse rate-limit (per room and second: 0.3 allowed)
                    await asyncio.sleep(3.4)
                    await self.room_methods.kick_user(room_id, mxid)
                kicked_users.append(mxid)
        return invited_users, kicked_users

    async def ensure_room_power_levels(
            self, room_id: RoomID, user_info_map: UserInfoMap, permissions: PowerLevelStateEventContent = None
    ) -> bool:
        """Ensures that given permissions are configured for a room

        :param room_id: Room id
        :param user_info_map: Map of users and their permissions
        :param permissions: Pre-defined permissions ( != power_levels)
        :return: Returns true if succeeded
        """
        current_state: StateEventContent = await self.room_methods.get_state_event(
            room_id, EventType.ROOM_POWER_LEVELS
        )
        current_power_levels: dict[UserID, int] = current_state["users"]
        current_users_default: int = current_state["users_default"]
        # self.logger.debug(f"Current power levels: {str(current_power_levels)}")
        change: bool = False
        for mxid in user_info_map:
            new_power_level = user_info_map[mxid]["power_level"]
            # I may not downgrade other admins :/
            if current_power_levels.get(UserID(mxid),
                                        current_users_default) != PowerLevel.ADMIN.value or mxid == self.client.mxid:
                change = True if current_power_levels.get(UserID(mxid),
                                                          current_users_default) != new_power_level else change
                if change:
                    current_power_levels[UserID(mxid)] = new_power_level
        # self.logger.debug(f"New power levels: {str(current_power_levels)}")
        permissions = current_state if not permissions else permissions
        permissions.users = current_power_levels
        change = True if permission_diff(current_state, permissions) else change
        if change:
            await self.room_methods.send_state_event(
                room_id,
                EventType.ROOM_POWER_LEVELS,
                permissions
            )
            self.logger.debug(f"Successfully ensured new power levels and permissions")
        else:
            self.logger.debug(f"No difference in power levels and permissions")
        return True

    async def get_power_level(self, room_id: RoomID, user_id: UserID, current_state: StateEventContent = None) -> int:
        """Returns the power_level of a given user in a given room

        :param current_state: (Optional) Current power_level state
        :param room_id: Room id
        :param user_id: User id
        :return: Power level (integer)
        """
        if not current_state:
            current_state = await self.room_methods.get_state_event(
                room_id, EventType.ROOM_POWER_LEVELS
            )
        current_power_levels: dict[UserID, int] = current_state["users"]
        current_users_default: int = current_state["users_default"]  # default power level for all users in room

        # self.logger.debug(f"Current power levels: {str(current_power_levels)}")
        # self.logger.debug(f"Current users_default: {str(current_users_default)}")

        user_power_level = current_power_levels.get(user_id, current_users_default)

        return user_power_level

    async def ensure_history_visibility(self, room_id: RoomID, visibility: str):
        """Ensures a specified history visibility

        :param room_id: The room id
        :param visibility: One of world_readable, shared, invited, joined (https://spec.matrix.org/latest/client-server-api/#room-history-visibility)
        :return: Nothing
        """
        current_state = await self.room_methods.get_state_event(
            room_id, EventType.ROOM_HISTORY_VISIBILITY
        )
        # self.logger.debug(f"Current history visibility: {current_state.get('history_visibility')}")
        if current_state.get('history_visibility') != visibility:
            content = {
                'history_visibility': visibility
            }
            await self.room_methods.send_state_event(
                room_id,
                EventType.ROOM_HISTORY_VISIBILITY,
                content
            )
            self.logger.debug(f"Changed history visibility of {room_id} to {visibility}")

    async def room_is_manageable(self, client: MaubotMatrixClient, room_id: RoomID,
                                 joined_rooms: List[RoomID] = None) -> bool:
        """Returns if all conditions are met by the room to be managed by the bot.
        - The bot must be joined
        - The bot must have sufficient power_level (admin)

        :param client: The bot client
        :param room_id: The room to be checked
        :param joined_rooms: (optional) List with joined rooms, makes request faster
        :return: If room is manageable by the bot or not
        """
        joined_rooms = await client.get_joined_rooms() if not joined_rooms else joined_rooms
        if room_id in joined_rooms and \
                (await self.get_power_level(room_id, UserID(client.mxid)) >= PowerLevel.MODERATOR.value):
            # self.logger.debug(f"Room is manageable: {room_id}")
            return True
        else:
            # self.logger.debug(f"Room is not manageable: {room_id}")
            return False

    async def ensure_room_is_leavable(self, room_id: RoomID, new_admin: str) -> bool:
        """Checks if bot may leave the room.
        If there are other administrator, this is safe.
        Then, the bots power-level is decreased. This is important, because the power-level
        persists over multiple room membership changes.

        :param room_id: Room id
        :param new_admin: MXID as string of the new admin
        :return: If room may be left or not
        """
        # ensure there is another admin (at least 2 admins in total)
        room_member_events = await self.event_methods.get_members(room_id)
        room_members, room_invitees = self.state_events_to_member_list(
            room_member_events
        )

        # New admin should have already accepted the invite
        if new_admin and new_admin not in room_members:
            return False

        admin_count = 0
        for member in room_members:
            if await self.get_power_level(room_id=room_id, user_id=UserID(member)) == PowerLevel.ADMIN.value:
                admin_count += 1

        if admin_count >= 2:
            # decrease own power level to standard
            user_info: UserInfo = {'power_level': PowerLevel.STANDARD.value}
            user_info_map: UserInfoMap = {self.client.mxid: user_info}
            await self.ensure_room_power_levels(room_id, user_info_map)
            return True
        else:
            return False

    async def user_is_kickable(self, room_id: RoomID, user_id: UserID, power_level_state: StateEventContent = None,
                               kick_external: bool = False) -> bool:
        """Check, if user may be kicked out of the room by the bot
        Conditions:
          - user may not be admin
          - bot should not kick itself
          - user must not be on another homeserver than the bot is

        :param kick_external: If true, users are kickable even if they are on an external homeserver
        :param room_id: Room id
        :param user_id: User id that should be checked on
        :param power_level_state: Pre-fetched power-level-state to speed up things
        :return: Returns True if a users meets the "kickable" conditions
        """
        # self.logger.debug(f"Checking, if {user_id} should be removed...")
        # User may not be admin
        if not power_level_state:
            power_level_state = await self.room_methods.get_state_event(
                room_id, EventType.ROOM_POWER_LEVELS
            )
        power_level = await self.get_power_level(room_id, user_id, power_level_state)
        not_admin: bool = power_level < PowerLevel.ADMIN.value
        if not not_admin:
            self.logger.debug(f"User {user_id} is admin and therefore NOT KICKABLE.")

        # User may not be bot
        not_bot: bool = user_id != self.client.mxid
        if not not_bot:
            self.logger.debug(f"I am user {user_id} and therefore NOT KICKABLE.")

        # User must be managed by the bot (same homeserver)
        # Exception: kick_external parameter is true
        mxid_user: MXID = MXID.from_str(user_id)
        mxid_bot: MXID = MXID.from_str(self.client.mxid)
        same_homeserver: bool = mxid_user.homeserver == mxid_bot.homeserver
        if not same_homeserver and not kick_external:
            self.logger.debug(f"User {user_id} is not on my homeserver and therefore NOT KICKABLE.")

        return not_admin and not_bot and (same_homeserver or kick_external)

    async def invite_user(self, room_alias: str, user: str, power_level: PowerLevel) -> str:
        """Invite a user into a room with a specified power_level if possible (aka the room is manageable)

        :param room_alias: Room alias of the room to invite into
        :param user: User that should be invited into the room
        :param power_level: The power level of the newly-invited user
        :return: A message that can be sent back to the user.
        """
        message = f"Successful: {user} was invited to {room_alias}."
        room_id = None

        try:
            room_id = await self.client.resolve_room_alias(RoomAlias(room_alias))
        except (mautrix.errors.MNotFound, mautrix.errors.MUnknown) as err:
            self.logger.debug(f"room not found: {room_alias}")
            self.logger.error(err)

        joined_rooms: List[RoomID] = await self.client.get_joined_rooms()
        if not await self.room_is_manageable(self.client, room_id.room_id, joined_rooms):
            return f"This room is not manageable ({room_alias})"
        else:
            if room_id:
                user_info: UserInfo = {'power_level': power_level.value}
                user_info_map: UserInfoMap = {user: user_info}
                await self.ensure_room_membership(room_id.room_id, user_info_map)
                await self.ensure_room_power_levels(room_id.room_id, user_info_map)
            else:
                message = "Room not found."
        return message

    async def get_room_name(self, room_id) -> str:
        """Returns the room name of a room.

        :param room_id: Room id
        :return: The current room name. Returns an empty string if no room name is set.
        """
        try:
            current_state = await self.room_methods.get_state_event(room_id, EventType.ROOM_NAME)
            return current_state['name']
        except MNotFound as e:
            return ""

    async def set_standard_room_name(self, room_id) -> bool:
        """Sets a standard room name retrieved from the room alias if no room name is set.

        :param room_id: Room id
        :return: Whether a new room name was set
        """
        current_room_name = await self.get_room_name(room_id)
        if len(current_room_name) < 1:
            try:
                current_state: CanonicalAliasStateEventContent = await self.room_methods.get_state_event(
                    room_id, EventType.ROOM_CANONICAL_ALIAS
                )
            except MNotFound:
                self.logger.debug(f"The room {room_id} has no alias. Standard room name can not be set.")
                return False
            alias: StructRoomAlias = StructRoomAlias.from_str(current_state.canonical_alias)
            new_name = alias.name.replace("_", " ")
            content = RoomNameStateEventContent(new_name)
            await self.room_methods.send_state_event(room_id, EventType.ROOM_NAME, content)
            return True
        else:
            return False


class EnsureRoomWithAliasException(Exception):
    pass
