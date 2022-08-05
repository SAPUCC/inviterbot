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

from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

from inviter.room_structure import MXID


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("idp_type")
        helper.copy("azure_ad")
        helper.copy("mxid_homeserver")
        helper.copy("ldap")
        helper.copy("administration_room")
        helper.copy("permissions")
        helper.copy("history_visibility")
        helper.copy("encryption_on_room_creation")
        helper.copy("cautious")
        helper.copy("renamed_users")

    def get_renamed_mxid(self, mxid: MXID) -> MXID:
        """Updates a MXID object to use a new username which can be configured in the config.
        Use-case: A user gets renamed in the identity provider but MXID stays the same.

        :param mxid: The mxid from the idp
        :return: The correct mxid after applying mapping from config
        """
        renamed_users = self.get("renamed_users", {})
        if renamed_users.get(mxid.username):
            logging.getLogger("maubot").debug(f"Mapped user {mxid.username} to {renamed_users.get(mxid.username)}")
            mxid.username = renamed_users.get(mxid.username)
        return mxid
