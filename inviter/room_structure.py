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

from typing import List, Optional, TypedDict, Mapping

from mautrix.types import RoomID

from inviter.enum import PowerLevel


class UserInfo(TypedDict):
    power_level: Optional[int]


UserInfoMap = Mapping[str, UserInfo]


class RoomAlias:
    """Represents a room alias like `#room:homeserver.com` that consists of name and homeserver.
    """
    name: str = None
    homeserver: str = None

    def __init__(self, name: str, homeserver: str):
        self.name = name
        self.homeserver = homeserver

    @classmethod
    def from_str(cls, alias_str: str):
        """Creates and returns a RoomAlias object from an alias_string

        :param alias_str: A string representing a room alias (e.g. #name:homeserver.tld)
        :return: Newly created RoomAlias object
        """
        name = alias_str.split(':', 1)[0].replace('#', '')
        homeserver = alias_str.split(':', 1)[1]
        return cls(name, homeserver)

    def __str__(self):
        return '#{name}:{homeserver}'.format(name=self.name, homeserver=self.homeserver)


class MXID:
    """Represents a matrix ID like `@user:homeserver.com` that consists of a username and homeserver

    """
    username: str = None
    homeserver: str = None

    def __init__(self, username: str, homeserver: str):
        self.username = username
        self.homeserver = homeserver

    @classmethod
    def from_str(cls, user_id: str):
        """Creates and returns a MXID object from a user_id string

        :param user_id: A string representing a MXID (e.g. @name:homeserver.tld)
        :return: Newly created MXID object
        """
        username: str = user_id.split(':', 1)[0].replace('@', '')
        homeserver: str = user_id.split(':', 1)[1]
        return cls(username, homeserver)

    def __str__(self):
        return '@{username}:{homeserver}'.format(username=self.username, homeserver=self.homeserver)


class RoomMember:
    """Represents room members including their MXID and power level
    """
    mxid: MXID = None
    power_level: int = None

    def __init__(self, mxid: MXID, power_level: PowerLevel):
        self.mxid = mxid
        self.power_level = power_level.value

    def __str__(self):
        return f"{self.mxid.__str__()} ({self.power_level})"


class Room:
    """Represents a room including the RoomAlias, RoomID and RoomMembers.
    Can be converted into a UserInfoMap object.
    """
    alias: RoomAlias = None
    id: RoomID = None
    members: List[RoomMember] = []

    def __init__(self, alias: RoomAlias, members: List[RoomMember]):
        self.alias = alias
        self.members = members

    def __str__(self):
        text = self.alias.__str__()
        for member in self.members:
            text = ("{text}\n - {member}".format(text=text, member=member))
        return text

    def __eq__(self, other):
        return str(self.alias) == str(other) or str(self.id) == str(other)

    def get_user_info_map(self) -> UserInfoMap:
        """Converts the room object into a UserInfoMap which is a mapping containing the member MXIDs and
        their power-levels

        :return: UserInfoMap object
        """
        user_map = {}
        for member in self.members:
            user_map[str(member.mxid)] = UserInfo(power_level=member.power_level)
        return user_map


