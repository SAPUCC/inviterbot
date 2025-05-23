![GitHub release (latest by date)](https://img.shields.io/github/v/release/SAPUCC/inviterbot)

# Maubot Inviterbot for Azure AD and LDAP
A [maubot](https://github.com/maubot) plugin that syncs user-groups from an identity management like Microsoft Azure Active Directory or LDAP to [Matrix](https://matrix.org/) rooms.
This way you can create managed rooms where users are automatically invited or kicked depending on their membership in a specific user-group. Permission-levels like "Standard" or "Moderator" as well as room permissions are also managed by the bot.

You might be also interested in this project, which is quite similiar: <https://github.com/davidmehren/maubot-ldap-inviter>

### Features
- Automatic sync of user groups from an identity management like LDAP or Microsoft Azure Active Directory to members of matrix rooms.
- Set permissions and roles for all managed matrix room
- Management room for debugging and manual syncing

![Inviter-Bot1](https://user-images.githubusercontent.com/8049779/187029596-8534d5ab-64ac-4352-9d69-66300dc416d3.png)

## Installation
This is a plugin for the Maubot bot system which is required for the bot to run.

It is also required to install the following additional dependencies to your maubot server environment. You can install them all with `pip install -r requirements.txt`:
- azure-identity
- msgraph-core
- ldap3

Otherwise use the [Dockerfile](https://github.com/SAPUCC/inviterbot/blob/main/Dockerfile) to have the requirements already installed.

Example `docker-compose.yml` for setting up the Maubot server:
````yaml
services:
  maubot:
    build:
      dockerfile: ./Dockerfile
      context: .
    container_name: maubot
    image: dock.mau.dev/maubot/maubot
    restart: unless-stopped
    volumes:
    - ./logs/:/var/log/maubot
    - ./data:/data
    ports:
      - 29316:29316
````

In your Maubot server Web-UI do the following:
- Load the *.mbp file from releases into your Maubot Manager (can also be generated from source with `mbc build`)
- Create client and instance in Maubot Manager

### Log levels
The log levels are very verbose per default. This can lead to many log messages especially from the microsoft azure library.
You may update the `config.yaml` to use INFO instead of debug.

````yaml
[...]
# Python logging configuration.
#
# See section 16.7.2 of the Python documentation for more info:
# https://docs.python.org/3.6/library/logging.config.html#configuration-dictionary-schema
logging:
    version: 1
    formatters:
        colored:
            (): maubot.lib.color_log.ColorFormatter
            format: "[%(asctime)s] [%(levelname)s@%(name)s] %(message)s"
        normal:
            format: "[%(asctime)s] [%(levelname)s@%(name)s] %(message)s"
    handlers:
        file:
            class: logging.handlers.RotatingFileHandler
            formatter: normal
            filename: ./maubot.log
            maxBytes: 10485760
            backupCount: 10
        console:
            class: logging.StreamHandler
            formatter: colored
    loggers:
        maubot:
            level: INFO
        mau:
            level: INFO
        aiohttp:
            level: INFO
        azure.identity._internal.get_token_mixin:
            level: INFO
        urllib3.connectionpool:
            level: INFO
        msal.application:
            level: INFO
        msal.telemetry:
            level: INFO
    root:
        level: DEBUG
        handlers: [file, console]
````


## Setup
One bot instance can only manage on identity provider at a time.
### Azure Active Directory
#### API setup
Follow the steps decribed [here](https://docs.microsoft.com/en-us/azure/active-directory/develop/scenario-daemon-app-registration).
- Create a Daemon App Registration
- Create a Client Secret
- Ensure that proper application permissions are given to the app registration. Don't forget to grant your admin consent.
  - `Group.Read.All`
  - `User.Read.All`

#### Example user-group structure
Create user-groups in your Azure AD like shown below. The users are fetched recursively, so you can put user-groups in user-groups.
````
Chat
├─ MTRX #example:matrix.example.com
│  ├─ User-Group 1
│  │  ├─ User A (Owner)
│  │  ├─ User B
│  ├─ User Group 2
│  │  ├─ User A
│  │  ├─ User C
│  │  ├─ User D
├─ MTRX #example2:matrix.example.com
│  ├─ User Group 3
│  │  ├─ User C (Owner)
│  │  ├─ User E
````

![Inviter-Bot2](https://user-images.githubusercontent.com/8049779/187029651-8f4b81ce-1ae7-4c19-865b-6445e86941eb.png)

### LDAP
#### Setup
- Create an LDAP bind account (read only)

#### Example user-group structure
Create user-groups in your LDAP like shown below. The `xxx` is used as a replacement for `{@, :}`, because these symbols are not allowed in LDAP.
````
xxxexamplexxxmatrix.example.com
├─ User-Group 1
│  ├─ User A
│  ├─ User B
├─ User Group 2
│  ├─ User A
│  ├─ User C
│  ├─ User D
xxxexamplexxxmatrix.example.com_owners
├─ User A
xxxexample2xxxmatrix.example.com
├─ User Group 3
│  ├─ User C
│  ├─ User E
xxxexample2xxxmatrix.example.com_owners
├─ User C
````

### Configuration
Configure the Inviterbot in the configuration on the Maubot server.

## Usage
### How to manage a matrix room
1. Create a user group in your linked Identity Provider as described above.
2. Syncs are performed automatically every 30 minutes

### How to manage a space
The bot can not create spaces on his own. Spaces that should be managed by the bot have to be created beforehand via Element.
1. Create a space
2. Invite the bot and give him Admin permission
3. Make the space public
4. Create an alias
5. Make it private again
6. Create a user group in your linked Identity Provider

### How to add external users to a managed room
#### Option 1: Two InviterBots in one room (fully managed)
Each bot only manages users from his homeserver. If the external users are managed by a bot on their homeserver, 
their bot can manage them in this room too. This would result in the following setup:

Some Room
- `@user1:homeserverA` is managed by bot A
- `@user2:homeserverA` is managed by bot A
- `@user3:homeserverB` is managed by bot B
- `@bot:homeserverA`   is a bot and admin and manages User 1 and User 2
- `@bot:homeserverB`   is a bot and admin and manages User 3

To achieve this
1. Make sure, that a user-group corresponding to the room-alias is defined in the identity providers of both bots
2. Invite the second bot to the room and give him admin permission
   (e.g. via `!invite-admin <room-alias> <user>`)
3. The second bot will now begin to sync the state of his idp to the room.

#### Option 2: Manually invite external users (partly unmanaged)
This is useful, if only a very small amount of external users should participate in a room. 

Some Room
- `@user1:homeserverA` is managed by bot A
- `@user2:homeserverA` is managed by bot A
- `@user3:homeserverB` is not managed
- `@bot:homeserverA`   is a bot and admin and manages User 1 and User 2

To achieve this
1. Use `!invite-member <room-alias> <user>` to invite an external user to the room that is managed by your bot.
2. Your bot will ignore this external user. If you want to kick him, this has to be done manually via `!kick-member <room-alias> <user>`

### Commands
Commands can only be sent to the bot in a specific administration room.
- Create admin room
- Invite bot
- Configure admin room in bot-configuration on the Maubot server

Use `!inviter` to list available commands.

* `!idp` - list rooms and members defined in the linked IdP
* `!joined` - list joined rooms
* `!managed` - list managed rooms
* `!sync [dry]` - trigger manual sync
* `!unmanage <room-alias> <new admin>` - unmanage room
* `!invite-member <room-alias> <user>` - manually add an external member as unmanaged standard user
* `!kick-member <room-alias> <user>` - manually kick an unmanaged, external member
* `!invite-admin <room-alias> <user>` - manually add an external member as admin (Be aware that admins can not be removed. They have to leave by their own.)

## Code and contributing
We would love to see your contribution to this project. Please refer to `CONTRIBUTING.md` for further details.

### Used libraries/modules
- [ldap3](https://github.com/cannatag/ldap3)
- [azure-identity]() - [docs](https://docs.microsoft.com/en-us/python/api/azure-identity/?view=azure-python)
- [msgraph-core](https://github.com/microsoftgraph/msgraph-sdk-python-core)
- [maubot](https://github.com/maubot/maubot)
- [mautrix-python](https://github.com/mautrix/python)

### Tips for development
- Use the [ms graph explorer](https://developer.microsoft.com/en-us/graph/graph-explorer) to get known to the graph API
- Read the [Microsoft Graph REST API reference](https://docs.microsoft.com/en-us/graph/api/overview?view=graph-rest-1.0)
- Read the [Matrix Client-Server API spec](https://spec.matrix.org/latest/client-server-api/)

### File structure
````
├── README.md
├── base-config.yaml    # Sample config that is loaded to the bot-instance on first start
├── inviter
│ ├── bot.py    # Main bot class with all command related functions
│ ├── bot_helper.py   # Static helper-functions
│ ├── config.py       # Maubot Config class that defines how to react on config updates
│ ├── enum.py         # Holds globally defined power-level definitions
│ ├── ldap_connector.py       # Everything related to the LDAP connection
│ ├── matrix_utils.py         # Everything directly related to the matrix Client-Server API
│ ├── ms_graph_connector.py   # Everything related to the MS graph API connection for Azure Active Directory
│ └── room_structure.py       # Data classes for room structure
└──  maubot.yaml      # Informations about the plugin
````

### CI
The release is created automatically via GitHub Actions. The workflow also includes building a .mbp plugin file and attaching it to the release. It is triggered only on main. To create a new release,
  1. Update the version in the maubot.yaml file
  2. Create a new git tag: `git tag -a v0.0.1 -m "my version 0.0.1"`
  3. Push the new tag to github and see the workflow to his job: `git push --follow-tags`

## Docs
For now, please refer to the docstrings in the code.

## License
This project is licensed under GPLv3. See `LICENSE` for the license text and `COPYRIGHT.md` for the general copyright notice.

