from httpx import AsyncClient
from apscheduler.triggers.interval import IntervalTrigger
from asyncio import (
    Lock,
    sleep,
    gather
)
from datetime import datetime, timedelta
from feedparser import parse as feedparse
from io import BytesIO
from nekozee.filters import (
    command,
    regex
)
from nekozee.handlers import (
    MessageHandler,
    CallbackQueryHandler
)
from nekozee.errors import (
    ListenerTimeout,
    ListenerStopped
)

from bot import (
    scheduler,
    rss_dict,
    LOGGER,
    DATABASE_URL,
    config_dict,
    bot
)
from bot.helper.ext_utils.bot_utils import arg_parser
from bot.helper.ext_utils.db_handler import DbManager
from bot.helper.ext_utils.exceptions import RssShutdownException
from bot.helper.ext_utils.help_messages import RSS_HELP_MESSAGE
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import (
    sendMessage,
    editMessage,
    sendRss,
    sendFile,
    deleteMessage,
)

rss_dict_lock = Lock()


async def rssMenu(event):
    user_id = event.from_user.id
    buttons = ButtonMaker()
    buttons.ibutton(
        "Subscribe",
        f"rss sub {user_id}"
    )
    buttons.ibutton(
        "Subscriptions",
        f"rss list {user_id} 0"
    )
    buttons.ibutton(
        "Get Items",
        f"rss get {user_id}"
    )
    buttons.ibutton(
        "Edit",
        f"rss edit {user_id}"
    )
    buttons.ibutton(
        "Pause",
        f"rss pause {user_id}"
    )
    buttons.ibutton(
        "Resume",
        f"rss resume {user_id}"
    )
    buttons.ibutton(
        "Unsubscribe",
        f"rss unsubscribe {user_id}"
    )
    if await CustomFilters.sudo(
        "", # type: ignore
        event
    ):
        buttons.ibutton(
            "All Subscriptions",
            f"rss listall {user_id} 0"
        )
        buttons.ibutton(
            "Pause All",
            f"rss allpause {user_id}"
        )
        buttons.ibutton(
            "Resume All",
            f"rss allresume {user_id}"
        )
        buttons.ibutton(
            "Unsubscribe All",
            f"rss allunsub {user_id}"
        )
        buttons.ibutton(
            "Delete User",
            f"rss deluser {user_id}"
        )
        if scheduler.running:
            buttons.ibutton(
                "Shutdown Rss",
                f"rss shutdown {user_id}"
            )
        else:
            buttons.ibutton(
                "Start Rss",
                f"rss start {user_id}"
            )
    buttons.ibutton(
        "Close",
        f"rss close {user_id}"
    )
    button = buttons.build_menu(2)
    msg = f"Rss Menu | Users: {len(rss_dict)} | Running: {scheduler.running}"
    return (
        msg,
        button
    )


async def updateRssMenu(query):
    msg, button = await rssMenu(query)
    await editMessage(
        query.message,
        msg,
        button
    )


async def getRssMenu(client, message):
    await client.stop_listening(
        chat_id=message.chat.id,
        user_id=message.from_user.id
    )
    (
        msg,
        button
    ) = await rssMenu(message)
    await sendMessage(
        message,
        msg,
        button
    )


async def rssSub(message):
    user_id = message.from_user.id
    if username := message.from_user.username:
        tag = f"@{username}"
    else:
        tag = message.from_user.mention
    msg = ""
    items = message.text.split("\n")
    for index, item in enumerate(
        items,
        start=1
    ):
        args = item.split()
        if len(args) < 2:
            await sendMessage(
                message,
                f"{item}. Wrong Input format. Read help message before adding new subcription!",
            )
            continue
        title = args[0].strip()
        if (
            (user_feeds := rss_dict.get(
                user_id,
                False
            ))
            and title in user_feeds
        ):
            await sendMessage(
                message,
                f"This title {title} already subscribed! Choose another title!"
            )
            continue
        feed_link = args[1].strip()
        if feed_link.startswith((
            "-inf",
            "-exf",
            "-c"
        )):
            await sendMessage(
                message,
                f"Wrong input in line {index}! Add Title! Read the example!",
            )
            continue
        inf_lists = []
        exf_lists = []
        if len(args) > 2:
            arg_base = {
                "-c": None,
                "-inf": None,
                "-exf": None,
                "-stv": None
            }
            arg_parser(
                args[2:],
                arg_base
            )
            cmd = arg_base["-c"]
            inf = arg_base["-inf"]
            exf = arg_base["-exf"]
            stv = arg_base["-stv"]
            if stv is not None:
                stv = stv.lower() == "true"
            if inf is not None:
                filters_list = inf.split("|")
                for x in filters_list: # type: ignore
                    y = x.split(" or ")
                    inf_lists.append(y)
            if exf is not None:
                filters_list = exf.split("|")
                for x in filters_list: # type: ignore
                    y = x.split(" or ")
                    exf_lists.append(y)
        else:
            inf = None
            exf = None
            cmd = None
            stv = False
        try:
            async with AsyncClient(verify=False) as client:
                res = await client.get(feed_link)
            html = res.text
            rss_d = feedparse(html)
            last_title = rss_d.entries[0]["title"]
            msg += "<b>Subscribed!</b>"
            msg += f"\n<b>Title: </b><code>{title}</code>\n<b>Feed Url: </b>{feed_link}"
            msg += f"\n<b>latest record for </b>{rss_d.feed.title}:"
            msg += (
                f"\nName: <code>{last_title.replace(
                    '>',
                    ''
                ).replace(
                    '<',
                    ''
                )}</code>"
            )
            try:
                last_link = rss_d.entries[0]["links"][1]["href"]
            except IndexError:
                last_link = rss_d.entries[0]["link"]
            msg += f"\nLink: <code>{last_link}</code>"
            msg += f"\n<b>Command: </b><code>{cmd}</code>"
            msg += f"\n<b>Filters:-</b>\ninf: <code>{inf}</code>\nexf: <code>{exf}</code>\n<b>sensitive: </b>{stv}"
            async with rss_dict_lock:
                if rss_dict.get(user_id, False):
                    rss_dict[user_id][title] = {
                        "link": feed_link,
                        "last_feed": last_link,
                        "last_title": last_title,
                        "inf": inf_lists,
                        "exf": exf_lists,
                        "paused": False,
                        "command": cmd,
                        "sensitive": stv,
                        "tag": tag,
                    }
                else:
                    rss_dict[user_id] = {
                        title: {
                            "link": feed_link,
                            "last_feed": last_link,
                            "last_title": last_title,
                            "inf": inf_lists,
                            "exf": exf_lists,
                            "paused": False,
                            "command": cmd,
                            "sensitive": stv,
                            "tag": tag,
                        }
                    }
            LOGGER.info(
                f"Rss Feed Added: id: {user_id} - title: {title} - link: {feed_link} - c: {cmd} - inf: {inf} - exf: {exf} - stv {stv}"
            )
        except (IndexError, AttributeError) as e:
            emsg = f"The link: {feed_link} doesn't seem to be a RSS feed or it's region-blocked!"
            await sendMessage(
                message,
                emsg + "\nError: " + str(e)
            )
        except Exception as e:
            await sendMessage(
                message,
                str(e)
            )
    if msg:
        if DATABASE_URL and rss_dict[user_id]:
            await DbManager().rss_update(user_id)
        await sendMessage(
            message,
            msg
        )
        is_sudo = await CustomFilters.sudo(
            "", # type: ignore
            message
        )
        if scheduler.state == 2:
            scheduler.resume()
        elif is_sudo and not scheduler.running:
            addJob()
            scheduler.start()


async def getUserId(title):
    async with rss_dict_lock:
        return next(
            (
                (True, user_id)
                for user_id, feed
                in rss_dict.items()
                if feed["title"] == title
            ),
            (
                False,
                False
            ),
        )


async def rssUpdate(message, state):
    user_id = message.from_user.id
    titles = message.text.split()
    is_sudo = await CustomFilters.sudo(
        "", # type: ignore
        message
    )
    updated = []
    for title in titles:
        title = title.strip()
        if not (res := rss_dict[user_id].get(title, False)):
            if is_sudo:
                res, user_id = await getUserId(title)
            if not res:
                user_id = message.from_user.id
                await sendMessage(
                    message,
                    f"{title} not found!"
                )
                continue
        istate = rss_dict[user_id][title].get("paused", False)
        if (
            istate and state == "pause"
            or not istate and state == "resume"
        ):
            await sendMessage(
                message,
                f"{title} already {state}d!"
            )
            continue
        async with rss_dict_lock:
            updated.append(title)
            if state == "unsubscribe":
                del rss_dict[user_id][title]
            elif state == "pause":
                rss_dict[user_id][title]["paused"] = True
            elif state == "resume":
                rss_dict[user_id][title]["paused"] = False
        if state == "resume":
            if scheduler.state == 2:
                scheduler.resume()
            elif is_sudo and not scheduler.running:
                addJob()
                scheduler.start()
        if (
            is_sudo
            and DATABASE_URL
            and user_id != message.from_user.id
        ):
            await DbManager().rss_update(user_id)
        if not rss_dict[user_id]:
            async with rss_dict_lock:
                del rss_dict[user_id]
            if DATABASE_URL:
                await DbManager().rss_delete(user_id)
                if not rss_dict:
                    await DbManager().trunc_table("rss")
    if updated:
        LOGGER.info(f"Rss link with Title(s): {updated} has been {state}d!")
        await sendMessage(
            message,
            f"Rss links with Title(s): <code>{updated}</code> has been {state}d!",
        )
        if (
            DATABASE_URL and
            rss_dict.get(user_id)
        ):
            await DbManager().rss_update(user_id)


async def rssList(query, start, all_users=False):
    user_id = query.from_user.id
    buttons = ButtonMaker()
    if all_users:
        list_feed = f"<b>All subscriptions | Page: {int(start / 5)} </b>"
        async with rss_dict_lock:
            keysCount = (
                sum(
                    len(v.keys())
                    for v in rss_dict.values()
                )
            )
            index = 0
            for titles in rss_dict.values():
                for index, (
                    title,
                    data
                ) in enumerate(
                    list(titles.items())[start : 5 + start]
                ):
                    list_feed += f"\n\n<b>Title:</b> <code>{title}</code>\n"
                    list_feed += f"<b>Feed Url:</b> <code>{data['link']}</code>\n"
                    list_feed += f"<b>Command:</b> <code>{data['command']}</code>\n"
                    list_feed += f"<b>Inf:</b> <code>{data['inf']}</code>\n"
                    list_feed += f"<b>Exf:</b> <code>{data['exf']}</code>\n"
                    list_feed += f"<b>Sensitive:</b> <code>{data.get('sensitive', False)}</code>\n"
                    list_feed += f"<b>Paused:</b> <code>{data['paused']}</code>\n"
                    list_feed += f"<b>User:</b> {data['tag'].replace('@', '', 1)}"
                    index += 1
                    if index == 5:
                        break
    else:
        list_feed = f"<b>Your subscriptions | Page: {int(start / 5)} </b>"
        async with rss_dict_lock:
            keysCount = len(rss_dict.get(user_id, {}).keys())
            for title, data in list(rss_dict[user_id].items())[start : 5 + start]:
                list_feed += f"\n\n<b>Title:</b> <code>{title}</code>\n<b>Feed Url: </b><code>{data['link']}</code>\n"
                list_feed += f"<b>Command:</b> <code>{data['command']}</code>\n"
                list_feed += f"<b>Inf:</b> <code>{data['inf']}</code>\n"
                list_feed += f"<b>Exf:</b> <code>{data['exf']}</code>\n"
                list_feed += (
                    f"<b>Sensitive:</b> <code>{data.get(
                        'sensitive',
                        False
                    )}</code>\n"
                )
                list_feed += f"<b>Paused:</b> <code>{data['paused']}</code>\n"
    buttons.ibutton(
        "Back",
        f"rss back {user_id}"
    )
    buttons.ibutton(
        "Close",
        f"rss close {user_id}"
    )
    if keysCount > 5:
        for x in range(
            0,
            keysCount,
            5
        ):
            buttons.ibutton(
                f"{int(x / 5)}",
                f"rss list {user_id} {x}",
                position="footer"
            )
    button = buttons.build_menu(2)
    if query.message.text.html == list_feed:
        return
    await editMessage(
        query.message,
        list_feed,
        button
    )


async def rssGet(message):
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        await sendMessage(
            message,
            f"{args}. Wrong Input format. You should add number of the items you want to get. Read help message before adding new subcription!",
        )
        return
    try:
        title = args[0]
        count = int(args[1])
        data = rss_dict[user_id].get(title, False)
        if data and count > 0:
            try:
                msg = await sendMessage(
                    message, f"Getting the last <b>{count}</b> item(s) from {title}"
                )
                async with AsyncClient(verify=False) as client:
                    res = await client.get(data["link"])
                html = res.text
                rss_d = feedparse(html)
                item_info = ""
                for item_num in range(count):
                    try:
                        link = rss_d.entries[item_num]["links"][1]["href"]
                    except IndexError:
                        link = rss_d.entries[item_num]["link"]
                    item_info += f"<b>Name: </b><code>{rss_d.entries[item_num]['title'].replace('>', '').replace('<', '')}</code>\n"
                    item_info += f"<b>Link: </b><code>{link}</code>\n\n"
                item_info_ecd = item_info.encode()
                if len(item_info_ecd) > 4000:
                    with BytesIO(item_info_ecd) as out_file:
                        out_file.name = f"rssGet {title} items_no. {count}.txt"
                        await sendFile(
                            message,
                            out_file
                        )
                    await deleteMessage(msg)
                else:
                    await editMessage(
                        msg,
                        item_info
                    )
            except IndexError as e:
                LOGGER.error(str(e))
                await editMessage(
                    msg,
                    "Parse depth exceeded. Try again with a lower value."
                )
            except Exception as e:
                LOGGER.error(str(e))
                await editMessage(
                    msg,
                    str(e)
                )
        else:
            await sendMessage(
                message,
                "Enter a valid title. Title not found!"
            )
    except Exception as e:
        LOGGER.error(str(e))
        await sendMessage(
            message,
            f"Enter a valid value!. {e}"
        )


async def rssEdit(message):
    user_id = message.from_user.id
    items = message.text.split("\n")
    updated = False
    for item in items:
        args = item.split()
        title = args[0].strip()
        if len(args) < 2:
            await sendMessage(
                message,
                f"{item}. Wrong Input format. Read help message before editing!",
            )
            continue
        elif not rss_dict[user_id].get(
            title,
            False
        ):
            await sendMessage(
                message,
                "Enter a valid title. Title not found!"
            )
            continue
        updated = True
        inf_lists = []
        exf_lists = []
        arg_base = {
            "-c": None,
            "-inf": None,
            "-exf": None,
            "-stv": None
        }
        arg_parser(
            args[1:],
            arg_base
        )
        cmd = arg_base["-c"]
        inf = arg_base["-inf"]
        exf = arg_base["-exf"]
        stv = arg_base["-stv"]
        async with rss_dict_lock:
            if stv is not None:
                stv = stv.lower() == "true"
                rss_dict[user_id][title]["sensitive"] = stv
            if cmd is not None:
                if cmd.lower() == "none":
                    cmd = None
                rss_dict[user_id][title]["command"] = cmd
            if inf is not None:
                if inf.lower() != "none":
                    filters_list = inf.split("|")
                    for x in filters_list: # type: ignore
                        y = x.split(" or ")
                        inf_lists.append(y)
                rss_dict[user_id][title]["inf"] = inf_lists
            if exf is not None:
                if exf.lower() != "none":
                    filters_list = exf.split("|")
                    for x in filters_list: # type: ignore
                        y = x.split(" or ")
                        exf_lists.append(y)
                rss_dict[user_id][title]["exf"] = exf_lists
    if DATABASE_URL and updated:
        await DbManager().rss_update(user_id)


async def rssDelete(message):
    users = message.text.split()
    for user in users:
        user = int(user)
        async with rss_dict_lock:
            del rss_dict[user]
        if DATABASE_URL:
            await DbManager().rss_delete(user)


async def event_handler(client, query):
    return await client.listen(
        chat_id=query.message.chat.id,
        user_id=query.from_user.id,
        timeout=60
    )


async def rssListener(client, query):
    user_id = query.from_user.id
    message = query.message
    data = query.data.split()
    if (
        int(data[2]) != user_id
        and not await CustomFilters.sudo(
            "", # type: ignore
            query
        )
    ):
        await query.answer(
            text="You don't have permission to use these buttons!",
            show_alert=True
        )
    elif data[1] == "close":
        await query.answer()
        await deleteMessage(message.reply_to_message)
        await deleteMessage(message)
    elif data[1] == "back":
        await query.answer()
        await updateRssMenu(query)
    elif data[1] == "sub":
        await query.answer()
        buttons = ButtonMaker()
        buttons.ibutton(
            "Back",
            f"rss back {user_id}"
        )
        buttons.ibutton(
            "Close",
            f"rss close {user_id}"
        )
        button = buttons.build_menu(2)
        await editMessage(
            message,
            RSS_HELP_MESSAGE,
            button
        )
        try:
            event = await event_handler(
                client,
                query
            )
        except ListenerTimeout:
            await updateRssMenu(query)
        except ListenerStopped:
            pass
        else:
            await gather(
                rssSub(event),
                updateRssMenu(query)
            )
    elif data[1] == "list":
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
        else:
            await query.answer()
            start = int(data[3])
            await rssList(
                query,
                start
            )
    elif data[1] == "get":
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.ibutton(
                "Back",
                f"rss back {user_id}"
            )
            buttons.ibutton(
                "Close",
                f"rss close {user_id}"
            )
            button = buttons.build_menu(2)
            await editMessage(
                message,
                "Send one title with value separated by space get last X items.\nTitle Value\nTimeout: 60 sec.",
                button,
            )
            try:
                event = await event_handler(
                    client,
                    query
                )
            except ListenerTimeout:
                await updateRssMenu(query)
            except ListenerStopped:
                pass
            else:
                await gather(
                    rssGet(event),
                    updateRssMenu(query)
                )
    elif data[1] in [
        "unsubscribe",
        "pause",
        "resume"
    ]:
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.ibutton(
                "Back",
                f"rss back {user_id}"
            )
            if data[1] == "pause":
                buttons.ibutton(
                    "Pause AllMyFeeds",
                    f"rss uallpause {user_id}"
                )
            elif data[1] == "resume":
                buttons.ibutton(
                    "Resume AllMyFeeds",
                    f"rss uallresume {user_id}"
                )
            elif data[1] == "unsubscribe":
                buttons.ibutton(
                    "Unsub AllMyFeeds",
                    f"rss uallunsub {user_id}"
                )
            buttons.ibutton(
                "Close",
                f"rss close {user_id}"
            )
            button = buttons.build_menu(2)
            await editMessage(
                message,
                f"Send one or more rss titles separated by space to {data[1]}.\nTimeout: 60 sec.",
                button,
            )
            try:
                event = await event_handler(
                    client,
                    query
                )
            except ListenerTimeout:
                await updateRssMenu(query)
            except ListenerStopped:
                pass
            else:
                await gather(
                    rssUpdate(
                        event,
                        data[1]
                    ),
                    updateRssMenu(query)
                )
    elif data[1] == "edit":
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.ibutton(
                "Back",
                f"rss back {user_id}"
            )
            buttons.ibutton(
                "Close",
                f"rss close {user_id}"
            )
            button = buttons.build_menu(2)
            msg = """Send one or more rss titles with new filters or command separated by new line.
Examples:
Title1 -c mirror -up remote:path/subdir -exf none -inf 1080 or 720 -stv true
Title2 -c none -inf none -stv false
Title3 -c mirror -rcf xxx -up xxx -z pswd -stv false
Note: Only what you provide will be edited, the rest will be the same like example 2: exf will stay same as it is.
Timeout: 60 sec. Argument -c for command and arguments
            """
            await editMessage(
                message,
                msg,
                button
            )
            try:
                event = await event_handler(
                    client,
                    query
                )
            except ListenerTimeout:
                await updateRssMenu(query)
            except ListenerStopped:
                pass
            else:
                await gather(
                    rssEdit(event),
                    updateRssMenu(query)
                )
    elif data[1].startswith("uall"):
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
            return
        await query.answer()
        if data[1].endswith("unsub"):
            async with rss_dict_lock:
                del rss_dict[int(data[2])]
            if DATABASE_URL:
                await DbManager().rss_delete(int(data[2]))
            await updateRssMenu(query)
        elif data[1].endswith("pause"):
            async with rss_dict_lock:
                for title in list(rss_dict[int(data[2])].keys()):
                    rss_dict[int(data[2])][title]["paused"] = True
            if DATABASE_URL:
                await DbManager().rss_update(int(data[2]))
        elif data[1].endswith("resume"):
            async with rss_dict_lock:
                for title in list(rss_dict[int(data[2])].keys()):
                    rss_dict[int(data[2])][title]["paused"] = False
            if scheduler.state == 2:
                scheduler.resume()
            if DATABASE_URL:
                await DbManager().rss_update(int(data[2]))
        await updateRssMenu(query)
    elif data[1].startswith("all"):
        if len(rss_dict) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
            return
        await query.answer()
        if data[1].endswith("unsub"):
            async with rss_dict_lock:
                rss_dict.clear()
            if DATABASE_URL:
                await DbManager().trunc_table("rss")
            await updateRssMenu(query)
        elif data[1].endswith("pause"):
            async with rss_dict_lock:
                for user in list(rss_dict.keys()):
                    for title in list(rss_dict[user].keys()):
                        rss_dict[int(data[2])][title]["paused"] = True
            if scheduler.running:
                scheduler.pause()
            if DATABASE_URL:
                await DbManager().rss_update_all()
        elif data[1].endswith("resume"):
            async with rss_dict_lock:
                for user in list(rss_dict.keys()):
                    for title in list(rss_dict[user].keys()):
                        rss_dict[int(data[2])][title]["paused"] = False
            if scheduler.state == 2:
                scheduler.resume()
            elif not scheduler.running:
                addJob()
                scheduler.start()
            if DATABASE_URL:
                await DbManager().rss_update_all()
    elif data[1] == "deluser":
        if len(rss_dict) == 0:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.ibutton(
                "Back",
                f"rss back {user_id}"
            )
            buttons.ibutton(
                "Close",
                f"rss close {user_id}")
            button = buttons.build_menu(2)
            msg = "Send one or more user_id separated by space to delete their resources.\nTimeout: 60 sec."
            await editMessage(
                message,
                msg,
                button
            )
            try:
                event = await event_handler(
                    client,
                    query
                )
            except ListenerTimeout:
                await updateRssMenu(query)
            except ListenerStopped:
                pass
            else:
                await gather(
                    rssDelete(event),
                    updateRssMenu(query)
                )
    elif data[1] == "listall":
        if not rss_dict:
            await query.answer(
                text="No subscriptions!",
                show_alert=True
            )
        else:
            await query.answer()
            start = int(data[3])
            await rssList(
                query,
                start,
                all_users=True
            )
    elif data[1] == "shutdown":
        if scheduler.running:
            await query.answer()
            scheduler.shutdown(wait=False)
            await sleep(0.5)
            await updateRssMenu(query)
        else:
            await query.answer(
                text="Already Stopped!",
                show_alert=True
            )
    elif data[1] == "start":
        if not scheduler.running:
            await query.answer()
            addJob()
            scheduler.start()
            await updateRssMenu(query)
        else:
            await query.answer(
                text="Already Running!",
                show_alert=True
            )


async def rssMonitor():
    if not config_dict["RSS_CHAT"]:
        scheduler.shutdown(wait=False)
        return
    if len(rss_dict) == 0:
        scheduler.pause()
        return
    all_paused = True
    for user, items in list(rss_dict.items()):
        for title, data in items.items():
            try:
                if data["paused"]:
                    continue
                tries = 0
                while True:
                    try:
                        async with AsyncClient(verify=False) as client:
                            res = await client.get(data["link"])
                        html = res.text
                        break
                    except:
                        tries += 1
                        if tries > 3:
                            raise
                        continue
                rss_d = feedparse(html)
                try:
                    last_link = rss_d.entries[0]["links"][1]["href"]
                except IndexError:
                    last_link = rss_d.entries[0]["link"]
                finally:
                    all_paused = False
                last_title = rss_d.entries[0]["title"]
                if data["last_feed"] == last_link or data["last_title"] == last_title:
                    continue
                feed_count = 0
                while True:
                    try:
                        await sleep(10)
                    except:
                        raise RssShutdownException("Rss Monitor Stopped!")
                    try:
                        item_title = rss_d.entries[feed_count]["title"]
                        try:
                            url = rss_d.entries[feed_count]["links"][1]["href"]
                        except IndexError:
                            url = rss_d.entries[feed_count]["link"]
                        if data["last_feed"] == url or data["last_title"] == item_title:
                            break
                    except IndexError:
                        LOGGER.warning(
                            f"Reached Max index no. {feed_count} for this feed: {title}. Maybe you need to use less RSS_DELAY to not miss some torrents"
                        )
                        break
                    parse = True
                    for flist in data["inf"]:
                        if (
                            data.get(
                                "sensitive",
                                False
                            )
                            and all(x.lower() not in item_title.lower() for x in flist)
                        ) or (
                            not data.get("sensitive", False)
                            and all(
                                x not in item_title
                                for x in flist
                            )
                        ):
                            parse = False
                            feed_count += 1
                            break
                    if not parse:
                        continue
                    for flist in data["exf"]:
                        if (
                            data.get("sensitive", False)
                            and any(
                                x.lower() in item_title.lower()
                                for x in flist
                            )
                        ) or (
                            data.get("sensitive", False)
                            and any(
                                x in item_title
                                for x in flist
                            )
                        ):
                            parse = False
                            feed_count += 1
                            break
                    if not parse:
                        continue
                    if command := data["command"]:
                        cmd = command.split(maxsplit=1)
                        cmd.insert(1, url)
                        feed_msg = " ".join(cmd)
                        if not feed_msg.startswith("/"):
                            feed_msg = f"/{feed_msg}"
                    else:
                        feed_msg = f"<b>Name: </b><code>{item_title.replace('>', '').replace('<', '')}</code>\n\n"
                        feed_msg += f"<b>Link: </b><code>{url}</code>"
                    feed_msg += (
                        f"\n<b>Powered By:</b> @Softleech"
                    )
                    await sendRss(feed_msg)
                    feed_count += 1
                async with rss_dict_lock:
                    if (
                        user not in rss_dict
                        or not rss_dict[user].get(
                            title,
                            False
                        )
                    ):
                        continue
                    rss_dict[user][title].update(
                        {
                            "last_feed": last_link,
                            "last_title": last_title
                        }
                    )
                await DbManager().rss_update(user)
                LOGGER.info(f"Feed Name: {title}")
                LOGGER.info(f"Last item: {last_link}")
            except RssShutdownException as ex:
                LOGGER.info(ex)
                break
            except Exception as e:
                LOGGER.error(f"{e} - Feed Name: {title} - Feed Link: {data['link']}")
                continue
    if all_paused:
        scheduler.pause()


def addJob():
    scheduler.add_job(
        rssMonitor,
        trigger=IntervalTrigger(seconds=config_dict["RSS_DELAY"]),
        id="0",
        name="RSS",
        misfire_grace_time=15,
        max_instances=1,
        next_run_time=datetime.now() + timedelta(seconds=20),
        replace_existing=True,
    )


addJob()
scheduler.start()
bot.add_handler( # type: ignore
    MessageHandler(
        getRssMenu,
        filters=command(
            BotCommands.RssCommand,
            case_sensitive=True
        ) & CustomFilters.authorized
    )
)
bot.add_handler( # type: ignore
    CallbackQueryHandler(
        rssListener,
        filters=regex("^rss")
    )
)
