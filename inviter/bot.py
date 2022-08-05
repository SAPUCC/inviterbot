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

import asyncio
from datetime import datetime, timedelta
from typing import Type

import mautrix
import pytz as pytz
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import RoomAlias, RoomID

from . import bot_helper as helper
from .bot_helper import MissingConfigException
from .config import Config
from .enum import PowerLevel
from .ldap_connector import LDAPConnectorMissingConfigException
from .matrix_utils import MatrixUtils, UserInfo, UserInfoMap, EnsureRoomWithAliasException
from .ms_graph_connector import MSGraphConnectorMissingConfigException
from .room_structure import Room, MXID

TZ = pytz.timezone('Europe/Berlin')


class InviterBot(Plugin):
    config: Config
    matrix_utils: MatrixUtils = None
    loop_task: asyncio.Future

    async def start(self) -> None:
        """Called when the plugin instance is starting.
        """
        self.config.load_and_update()
        self.matrix_utils = MatrixUtils(self.client, self.log)
        self.loop_task = asyncio.ensure_future(self.sync_loop(), loop=self.loop)

    async def stop(self) -> None:
        """Called when the plugin instance is stopping.
        """
        self.loop_task.cancel()

    async def sync_loop(self) -> None:
        """Schedules a sync every 30 minutes
        """
        try:
            self.log.debug(f"Sync loop started for {self.client.mxid}")
            while True:
                now = datetime.now(TZ)
                scheduled_time = now + timedelta(minutes=30)
                self.log.info(f"Scheduled sync for {scheduled_time} in {scheduled_time - now} seconds")
                await asyncio.sleep((scheduled_time - now).total_seconds())
                asyncio.create_task(self.auto_sync_rooms())
        except asyncio.CancelledError:
            self.log.warning(f"Sync loop stopped {self.client.mxid}")
        except Exception:
            self.log.error("Exception in sync loop")

    @command.new(name='inviter', help="Administration of inviter-bot", require_subcommand=False, arg_fallthrough=False)
    async def help(self, evt: MessageEvent) -> None:
        """Command to show help message with possible commands

        :param evt: Relating message event
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        message = ("**Inviter-bot commands:**\n\n"
                   "* `idp` - list rooms and members defined in IdP\n"
                   "* `joined` - list joined rooms\n"
                   "* `managed` - list managed rooms\n"
                   "* `sync [dry]` - trigger manual sync\n"
                   "* `unmanage <room-alias> [new admin]` - unmanage room\n"
                   "* `invite-member <room-alias> [user]` - manually add an external member as unmanaged standard user\n"
                   "* `kick-member <room-alias> [user]` - manually kick an unmanaged, external member\n"
                   "* `invite-admin <room-alias> [user]` - manually add an external member as admin (Be aware that admins can not be removed. They have to leave by their own.)\n")
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='idp')
    async def idp(self, evt: MessageEvent) -> None:
        """Command to show the identity provider content as the bot sees it

        :param evt: Relating message event
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        self.log.info("Getting rooms from identity provider...")
        await evt.respond(helper.generate_message_content("Getting rooms from identity provider..."))

        try:
            room_struct = await helper.get_room_struct_from_idp(self.config)
        except (MissingConfigException, MSGraphConnectorMissingConfigException, LDAPConnectorMissingConfigException):
            message = "The bot configuration is incomplete."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        message = f"**Room structure from {self.config.get('idp_type', '-')}:**\n"
        message += ''.join("\n" + str(element) + '\n' for element in room_struct)
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='joined')
    async def joined(self, evt: MessageEvent) -> None:
        """Command to list all rooms where the bot is joined

        :param evt: Relating message event
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        joined_rooms = await self.client.get_joined_rooms()
        message = "**Joined rooms:**\n" + ''.join(" - " + str(element) + '\n' for element in joined_rooms)
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='managed')
    async def managed(self, evt: MessageEvent) -> None:
        """Command to list all bot-managed rooms and their members

        :param evt: Relating message event
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        self.log.info("Getting managed rooms from identity provider...")
        await evt.respond(helper.generate_message_content("Getting managed rooms from identity provider..."))

        try:
            managed_rooms = await helper.get_managed_rooms(self.client, self.config, self.matrix_utils)
        except (MissingConfigException, MSGraphConnectorMissingConfigException, LDAPConnectorMissingConfigException):
            message = "The bot configuration is incomplete."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        self.log.debug(f"Managed room count {len(managed_rooms)}")
        message = "**Managed rooms:** (as in IdP)\n" + ''.join("\n" + str(element) + '\n' for element in managed_rooms)
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='sync')
    @command.argument("dry", "dry run", pass_raw=True, required=False, matches=r'dry')
    async def sync(self, evt: MessageEvent, dry: str) -> None:
        """Command to perform a manual sync. Optionally do a dry sync without applying changes.

        :param evt: Relating message event
        :param dry: The dry argument
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        inviting = kicking = True
        if dry and 'dry' in dry:
            inviting = kicking = False
            self.log.info("Performing a dry sync...")
            await evt.respond(helper.generate_message_content("Performing a dry sync..."))
        else:
            self.log.info("Performing a full sync...")
            await evt.respond(helper.generate_message_content("Performing a full sync..."))

        try:
            room_struct = await helper.get_room_struct_from_idp(self.config)
        except (MissingConfigException, MSGraphConnectorMissingConfigException, LDAPConnectorMissingConfigException):
            message = "The bot configuration is incomplete."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        for room in room_struct:
            message = await self.sync_room(room, inviting=inviting, kicking=kicking)
            # Use sleep to mitigate synapse rate-limiting
            await asyncio.sleep(4)
            await evt.respond(helper.generate_message_content(message))

    @command.new(name='unmanage')
    @command.argument("room_alias", "Room alias", pass_raw=True, required=True, matches=r'#.*?:\S*')
    @command.argument("admin", "New admin", pass_raw=True, required=False, matches=r'@.*?:\S*')
    async def unmanage(self, evt: MessageEvent, room_alias: str, admin: str) -> None:
        """Command to unmanage a bot-managed room.

        :param evt: Relating message event
        :param room_alias: Room to unmanage
        :param admin: MXID of the user that should be admin user
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        self.log.info(f"Unmanaging room {room_alias}...")
        self.log.debug(f"New admin for unmanaging {room_alias}: {admin}")
        message = "Successful: Room is not managed anymore."
        room_id = None

        try:
            managed_rooms = await helper.get_managed_rooms(self.client, self.config, self.matrix_utils)
        except (MissingConfigException, MSGraphConnectorMissingConfigException, LDAPConnectorMissingConfigException):
            message = "The bot configuration is incomplete."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        if room_alias not in managed_rooms:
            message = f"I am not managing this room. ({room_alias})"
            self.log.warning(message)
        else:
            try:
                room_id = await self.client.resolve_room_alias(RoomAlias(room_alias))
            except (mautrix.errors.MNotFound, mautrix.errors.MUnknown) as err:
                self.log.debug(f"Room not found: {room_alias}")
                self.log.error(err)
            if room_id:
                if admin:
                    user_info: UserInfo = {'power_level': PowerLevel.ADMIN.value}
                    user_info_map: UserInfoMap = {admin: user_info}
                    await self.matrix_utils.ensure_room_membership(room_id.room_id, user_info_map)
                    self.log.debug(f"Invited new admin ({admin}) to room {room_alias}.")
                    await self.matrix_utils.ensure_room_power_levels(room_id.room_id, user_info_map)
                    self.log.debug(f"Ensured room power levels for new admin ({admin}) in room {room_alias}.")
                if await self.matrix_utils.ensure_room_is_leavable(room_id.room_id, new_admin=admin):
                    await self.client.leave_room(room_id.room_id)
                    self.log.info(f"I left the room {room_alias}.")
                else:
                    self.log.warning(f"Room {room_alias} is not leavable without breaking the room.")
                    message = "Room is not leavable without breaking the room. " \
                              "Use `!unmanage <room-alias> <admin-mxid>` to define my successor." \
                              "If you already defined a successor, make sure that he accepted the invite."
            else:
                message = f"Room {room_alias} not found."
                self.log.warning(message)
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='invite-member')
    @command.argument("room_alias", "Room alias", pass_raw=True, required=True, matches=r'#.*?:\S*')
    @command.argument("user", "User", pass_raw=True, required=True, matches=r'@.*?:\S*')
    async def invite_member(self, evt: MessageEvent, room_alias: str, user: str) -> None:
        """Command to manually invite a user from a foreign homeserver.

        :param evt: Relating message event
        :param room_alias: Managed room where the user should be invited
        :param user: MXID of the user that should be invited
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        self.log.info(f"Inviting user {user} to room {room_alias}...")

        # User must not be managed by the bot (other homeserver)
        mxid_user: MXID = MXID.from_str(user)
        mxid_bot: MXID = MXID.from_str(self.client.mxid)
        same_homeserver: bool = mxid_user.homeserver == mxid_bot.homeserver
        if same_homeserver:
            message = f"User {user} is on my homeserver. Use the idp to add him to the room."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        message = await self.matrix_utils.invite_user(room_alias, user, power_level=PowerLevel.STANDARD)
        self.log.info(f"Invitation of user {user} to room {room_alias} was successful.")
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='kick-member')
    @command.argument("room_alias", "Room alias", pass_raw=True, required=True, matches=r'#.*?:\S*')
    @command.argument("user", "User", pass_raw=True, required=True, matches=r'@.*?:\S*')
    async def kick_member(self, evt: MessageEvent, room_alias: str, user: str) -> None:
        """Command to manually kick a user from a foreign homeserver

        :param evt: Relating message event
        :param room_alias: Managed room where the user should be invited
        :param user: MXID of the user that should be invited
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        self.log.info(f"Kicking user {user} from room {room_alias}...")

        # User must not be managed by the bot (other homeserver)
        mxid_user: MXID = MXID.from_str(user)
        mxid_bot: MXID = MXID.from_str(self.client.mxid)
        same_homeserver: bool = mxid_user.homeserver == mxid_bot.homeserver
        if same_homeserver:
            message = f"User {user} is on my homeserver. Use the idp to kick him from the room."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        message = f"Successful: {user} got kicked from {room_alias}."
        room_id = None

        try:
            managed_rooms = await helper.get_managed_rooms(self.client, self.config, self.matrix_utils)
        except (MissingConfigException, MSGraphConnectorMissingConfigException, LDAPConnectorMissingConfigException):
            message = "The bot configuration is incomplete."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        if room_alias not in managed_rooms:
            message = f"I am not managing this room. ({room_alias})"
            self.log.warning(message)
        else:
            try:
                room_id = await self.client.resolve_room_alias(RoomAlias(room_alias))
            except (mautrix.errors.MNotFound, mautrix.errors.MUnknown) as err:
                self.log.debug(f"Room not found: {room_alias}")
                self.log.error(err)
            if room_id:
                if await self.matrix_utils.user_is_kickable(room_id.room_id, user, kick_external=True):
                    await self.matrix_utils.room_methods.kick_user(room_id.room_id, user)
                    self.log.info(message)
                else:
                    message = "User is not kickable"
                    self.log.warning(message)
            else:
                message = f"Room not found: {room_alias}"
                self.log.warning(message)
        await evt.respond(helper.generate_message_content(message))

    @command.new(name='invite-admin')
    @command.argument("room_alias", "Room alias", pass_raw=True, required=True, matches=r'#.*?:\S*')
    @command.argument("user", "User", pass_raw=True, required=True, matches=r'@.*?:\S*')
    async def invite_admin(self, evt: MessageEvent, room_alias: str, user: str) -> None:
        """Command to manually invite an admin user from a foreign homeserver into a bot-managed room

        :param evt: Relating message event
        :param room_alias: Managed room where the user should be invited
        :param user: MXID of the user that should be invited
        :return:
        """
        if not await self.is_admin_room(evt):
            return

        self.log.info(f"Inviting user {user} as admin to room {room_alias}...")

        # User must not be managed by the bot (other homeserver)
        mxid_user: MXID = MXID.from_str(user)
        mxid_bot: MXID = MXID.from_str(self.client.mxid)
        same_homeserver: bool = mxid_user.homeserver == mxid_bot.homeserver
        if same_homeserver:
            message = f"User {user} is on my homeserver. Use the idp to add him to the room."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return

        message = await self.matrix_utils.invite_user(room_alias, user, power_level=PowerLevel.ADMIN)
        self.log.info(message)

        await evt.respond(helper.generate_message_content(message))

    async def auto_sync_rooms(self) -> None:
        """Auto-sync rooms from sync-loop

        :return: Nothing
        """
        room_struct = await helper.get_room_struct_from_idp(self.config)
        cautious = self.config.get("cautious", True)
        inviting = True
        kicking = not cautious
        for room in room_struct:
            message = await self.sync_room(room, inviting=inviting, kicking=kicking)
            self.log.info(message)

    async def sync_room(self, room: Room, inviting: bool, kicking: bool) -> str:
        """Sync a single Matrix room with a given room target-state

        :param room: Room object containing the target room state (members, permissions, etc.)
        :param inviting: Whether bot is allowed to perform invite actions
        :param kicking:Whether bot is allowed to perform kick actions
        :return: Info about performed actions
        """
        message = ""

        alias = str(room.alias)
        encryption_on_room_creation = self.config.get("encryption_on_room_creation", True)

        # Only create room if the homeserver of the room-alias matches the homeserver defined in the bot config
        may_create_room: bool = self.config.get('mxid_homeserver', None) == room.alias.homeserver
        self.log.debug(f"May create room {alias}: {may_create_room}")

        try:
            room_id = await self.matrix_utils.ensure_room_with_alias(alias, encryption_on_room_creation, may_create_room)
        except EnsureRoomWithAliasException:
            message = f"Room {alias} could neither be found nor created."
            self.log.warning(message)
            return message

        # Check if manageable
        if not await self.matrix_utils.room_is_manageable(self.client, room_id):
            message = f"Room {alias} is not manageable. Please invite me and make sure, that I got admin power-level."
            self.log.warning(message)
            return message
        else:
            message += f"Syncing room: {alias}\n\n"
            self.log.debug(f"Syncing room: {alias}")

            # Generate map of users
            all_users = room.get_user_info_map()

            # Ensure users are invited or kicked
            invited_users, kicked_users = await self.matrix_utils.ensure_room_membership(room_id,
                                                                                         all_users,
                                                                                         inviting=inviting,
                                                                                         kicking=kicking)
            # Generate information about performed membership actions
            membership_actions = ""
            if invited_users and len(invited_users) > 0:
                no_inviting: str = "" if inviting else "would have been "
                membership_actions = f"Users {no_inviting}invited:\n"
                for mxid in invited_users:
                    membership_actions += f"- {mxid}\n"
                membership_actions += "\n"
            if kicked_users and len(kicked_users) > 0:
                no_kicking: str = "" if kicking else "would have been "
                membership_actions += f"Users {no_kicking}kicked:\n"
                for mxid in kicked_users:
                    membership_actions += f"- {mxid}\n"
                membership_actions += "\n"
            message += membership_actions

            # Get permissions from config only if alias homeserver matches bot homeserver
            mxid_bot: MXID = MXID.from_str(self.client.mxid)
            if mxid_bot.homeserver == room.alias.homeserver:
                permissions = await helper.get_permissions_from_config(self.config)
                if not permissions:
                    message = f"Room {alias}: Failed to fetch room permissions from config."
                    self.log.warning(message)
                    return message
                # Ensure users have correct power levels and set permissions from config
                if not await self.matrix_utils.ensure_room_power_levels(room_id, all_users, permissions):
                    message = f"Room {alias}: Failed to sync room permissions and power levels."
                    self.log.warning(message)
                    return message
            else:
                # Ensure users have correct power levels
                if not await self.matrix_utils.ensure_room_power_levels(room_id, all_users):
                    message = f"Room {alias}: Failed to sync room permissions and power levels."
                    self.log.warning(message)
                    return f"Room {alias}: Failed to sync room permissions and power levels."

            # Ensure room history_visibility
            history_visibility = self.config.get("history_visibility", "shared")
            await self.matrix_utils.ensure_history_visibility(room_id, history_visibility)

            # Ensure room is (in) visible in Room Directory
            # await self.matrix_utils.ensure_room_visibility(room_id, room["visibility"])

            # Set a standard room name retrieved from the room alias if no room name is set yet
            await self.matrix_utils.set_standard_room_name(room_id)

            message += "successful ✅"
            self.log.debug(f"Room {alias}: Successfully synced room.")
            return message

    async def is_admin_room(self, evt: MessageEvent) -> bool:
        """Returns whether the bots actions is requested from within an admin room.
        Only members of the admin room should be able to control the bot.

        :param evt: The message event that was sent by a room member
        :return: Whether room is admin room or not
        """
        # Case: No admin room configured
        admin_room_alias: RoomAlias = RoomAlias(self.config.get("administration_room", None))
        if not admin_room_alias:
            message = "No administration room configured for this bot-instance."
            self.log.warning(message)
            await evt.respond(helper.generate_message_content(message))
            return False

        # Case: Configured admin room does not exist or bot not invited.
        try:
            admin_room_id: RoomID = RoomID((await self.client.resolve_room_alias(admin_room_alias)).room_id)
        except (mautrix.errors.MNotFound, mautrix.errors.MUnknown) as err:
            self.log.warning(f"Room not found: {admin_room_alias}")
            self.log.error(err)
            message = f"The administration room ({admin_room_alias}) defined in the config does not exist. " \
                      f"Please create this room and invite me."
            await evt.respond(helper.generate_message_content(message))
            self.log.warning(message)
            return False

        # Case: Everything okay: This is the admin room.
        if admin_room_id == evt.room_id:
            return True

        # Case: Not allowed: This is not the admin room.
        else:
            await evt.respond(helper.generate_message_content(f"This is not the admin-room. If you want to send me"
                                                              f" commands, please head over to {admin_room_alias}"))
            return False

    @classmethod
    def get_config_class(cls) -> Type[Config]:
        return Config
