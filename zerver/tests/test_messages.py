import datetime
from typing import Dict, List

import responses
from django.utils.timezone import now as timezone_now

from zerver.lib.actions import get_active_presence_idle_user_ids, get_client
from zerver.lib.message import access_message
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import (
    Message,
    UserPresence,
    UserProfile,
    bulk_get_huddle_user_ids,
    get_huddle_user_ids,
)


class MissedMessageTest(ZulipTestCase):
    def test_presence_idle_user_ids(self) -> None:
        UserPresence.objects.all().delete()

        sender = self.example_user("cordelia")
        realm = sender.realm
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        recipient_ids = {hamlet.id, othello.id}
        message_type = "stream"
        user_flags: Dict[int, List[str]] = {}

        def assert_missing(user_ids: List[int]) -> None:
            presence_idle_user_ids = get_active_presence_idle_user_ids(
                realm=realm,
                sender_id=sender.id,
                message_type=message_type,
                active_user_ids=recipient_ids,
                user_flags=user_flags,
            )
            self.assertEqual(sorted(user_ids), sorted(presence_idle_user_ids))

        def set_presence(user: UserProfile, client_name: str, ago: int) -> None:
            when = timezone_now() - datetime.timedelta(seconds=ago)
            UserPresence.objects.create(
                user_profile_id=user.id,
                realm_id=user.realm_id,
                client=get_client(client_name),
                timestamp=when,
            )

        message_type = "private"
        assert_missing([hamlet.id, othello.id])

        message_type = "stream"
        user_flags[hamlet.id] = ["mentioned"]
        assert_missing([hamlet.id])

        set_presence(hamlet, "iPhone", ago=5000)
        assert_missing([hamlet.id])

        set_presence(hamlet, "website", ago=15)
        assert_missing([])

        message_type = "private"
        assert_missing([othello.id])


class TestBulkGetHuddleUserIds(ZulipTestCase):
    def test_bulk_get_huddle_user_ids(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        iago = self.example_user("iago")
        message_ids = [
            self.send_huddle_message(hamlet, [cordelia, othello], "test"),
            self.send_huddle_message(cordelia, [hamlet, othello, iago], "test"),
        ]

        messages = Message.objects.filter(id__in=message_ids).order_by("id")
        first_huddle_recipient = messages[0].recipient
        first_huddle_user_ids = list(get_huddle_user_ids(first_huddle_recipient))
        second_huddle_recipient = messages[1].recipient
        second_huddle_user_ids = list(get_huddle_user_ids(second_huddle_recipient))

        huddle_user_ids = bulk_get_huddle_user_ids(
            [first_huddle_recipient, second_huddle_recipient]
        )
        self.assertEqual(huddle_user_ids[first_huddle_recipient.id], first_huddle_user_ids)
        self.assertEqual(huddle_user_ids[second_huddle_recipient.id], second_huddle_user_ids)

    def test_bulk_get_huddle_user_ids_empty_list(self) -> None:
        self.assertEqual(bulk_get_huddle_user_ids([]), {})


class TestServiceBotAccessMessage(ZulipTestCase):
    def create_outgoing_webhook_bot(self, bot_owner: UserProfile) -> UserProfile:
        return self.create_test_bot(
            "outgoing-webhook",
            bot_owner,
            "Outgoing Webhook Bot",
            bot_type=UserProfile.OUTGOING_WEBHOOK_BOT,
            service_name="foo-service",
            payload_url='"https://bot.example.com"',
        )

    def create_embedded_bot(self, bot_owner: UserProfile) -> UserProfile:
        return self.create_test_bot(
            "embedded-bot",
            bot_owner,
            "Embedded Bot",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name="helloworld",
        )

    @responses.activate
    def test_outgoing_webhook_bot_access_private_message(self) -> None:
        bot_owner = self.example_user("othello")
        bot = self.create_outgoing_webhook_bot(bot_owner)

        responses.add("POST", "https://bot.example.com", json={"content": "beep boop"})

        with self.assertLogs(level="INFO") as logs:
            self.send_personal_message(bot_owner, bot, content="foo")

        self.assert_length(responses.calls, 1)
        self.assert_length(logs.output, 1)
        self.assertIn(f"Outgoing webhook request from {bot.id}@zulip took ", logs.output[0])

        last_message = self.get_last_message()
        self.assertIsNotNone(access_message(bot, last_message.id))
        second_to_last_message = self.get_second_to_last_message()
        self.assertIsNotNone(access_message(bot, second_to_last_message.id))

    @responses.activate
    def test_outgoing_webhook_bot_access_huddle_message(self) -> None:
        bot_owner = self.example_user("othello")
        bot = self.create_outgoing_webhook_bot(bot_owner)
        other_user = self.example_user("hamlet")

        responses.add("POST", "https://bot.example.com", json={"content": "beep boop"})

        with self.assertLogs(level="INFO") as logs:
            self.send_huddle_message(bot_owner, [bot, other_user], "bar")

        self.assert_length(responses.calls, 1)
        self.assert_length(logs.output, 1)
        self.assertIn(f"Outgoing webhook request from {bot.id}@zulip took ", logs.output[0])

        last_message = self.get_last_message()
        self.assertIsNotNone(access_message(bot, last_message.id))
        second_to_last_message = self.get_second_to_last_message()
        self.assertIsNotNone(access_message(bot, second_to_last_message.id))

    def test_embedded_bot_access_private_message(self) -> None:
        bot_owner = self.example_user("othello")
        bot = self.create_embedded_bot(bot_owner)

        self.send_personal_message(bot_owner, bot, content="foo")

        last_message = self.get_last_message()
        self.assertIsNotNone(access_message(bot, last_message.id))
        second_to_last_message = self.get_second_to_last_message()
        self.assertIsNotNone(access_message(bot, second_to_last_message.id))

    def test_embedded_bot_access_huddle_message(self) -> None:
        bot_owner = self.example_user("othello")
        bot = self.create_embedded_bot(bot_owner)
        other_user = self.example_user("hamlet")

        self.send_huddle_message(bot_owner, [bot, other_user], "bar")

        last_message = self.get_last_message()
        self.assertIsNotNone(access_message(bot, last_message.id))
        second_to_last_message = self.get_second_to_last_message()
        self.assertIsNotNone(access_message(bot, second_to_last_message.id))
