import threading
from typing import TYPE_CHECKING, List, Optional

from django.db import connections
from django.utils.timezone import now as timezon_now

from zerver.actions.user_groups import add_subgroups_to_user_group
from zerver.lib.test_classes import ZulipTransactionTestCase
from zerver.models import UserGroup
from zerver.tests.test_user_groups import UserGroupTestMixin
from zerver.views.development import user_groups as user_group_view

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class UserGroupRaceConditionTestCase(UserGroupTestMixin, ZulipTransactionTestCase):
    def create_user_group_chain(self) -> List[UserGroup]:
        """Build a user groups forming a chain through group-group memberships
        returning a list where each group is the supergroup of its subsequent group.
        """
        groups = [self.create_user_group_for_test(f"chain #{timezon_now()}") for _ in range(3)]
        prev_group = groups[0]
        for group in groups[1:]:
            add_subgroups_to_user_group(prev_group, [group], acting_user=None)
            prev_group = group
        return groups

    def test_lock_subgroups_with_respect_to_supergroup(self) -> None:
        self.example_user("iago")
        self.login("iago")
        test_case = self

        class RacingThread(threading.Thread):
            def __init__(self, supergroup_id: int, subgroup_id: int) -> None:
                threading.Thread.__init__(self)
                self.response: Optional["TestHttpResponse"] = None
                self.supergroup_id = supergroup_id
                self.subgroup_id = subgroup_id

            def run(self) -> None:
                try:
                    self.response = test_case.client_post(
                        url=f"/testing/user_groups/{self.supergroup_id}/subgroups",
                        info={"add": f"{[self.subgroup_id]}"},
                    )
                finally:
                    # Close all thread-local database connections
                    connections.close_all()

        def assert_exactly_one_thread_fails(
            t1: RacingThread,
            t2: RacingThread,
            *,
            error_messsage: str,
            barrier: Optional[threading.Barrier] = None,
        ) -> None:
            help_msg = """We access the test endpoint that wraps around the real subgroup update endpoint
by sychronizing them after the acquisition of the first lock in the critical region.
Though unlikely, this test might fail as we have no control over the scheduler when the barrier timeouts.
""".strip()

            user_group_view.set_sync_after_first_lock(barrier)
            t1.start()
            t2.start()

            succeeded = 0
            for t in [t1, t2]:
                t.join()
                response = t.response
                if response is not None and response.status_code == 200:
                    succeeded += 1
                    continue

                assert response is not None
                self.assert_json_error(response, error_messsage)
            # Race condition resolution should only allow one thread to succeed
            self.assertEqual(succeeded, 1, f"Exactly one thread should succeed.\n{help_msg}")

        foo_chain = self.create_user_group_chain()
        bar_chain = self.create_user_group_chain()
        # These two threads are conflicting because a cycle would be form if both of them succeed.
        # Meanwhile, there is a deadlock because we use a Barrier to synchronize both threads to acquire
        # a lock on one chain of user groups before trying to acquire a lock on the other chain, respectively.
        assert_exactly_one_thread_fails(
            RacingThread(supergroup_id=bar_chain[-1].id, subgroup_id=foo_chain[0].id),
            RacingThread(supergroup_id=foo_chain[0].id, subgroup_id=bar_chain[-1].id),
            error_messsage="Deadlock detected",
            barrier=threading.Barrier(2, timeout=3),
        )

        foo_chain = self.create_user_group_chain()
        bar_chain = self.create_user_group_chain()
        # Both threads will attempt to grab a lock on overlapping rows when they first do the recursive query for subgroups.
        # In this case, we expect that one of the threads fails due to nowait=True for the .select_for_update() call.
        assert_exactly_one_thread_fails(
            RacingThread(supergroup_id=bar_chain[-1].id, subgroup_id=foo_chain[0].id),
            RacingThread(supergroup_id=bar_chain[-1].id, subgroup_id=foo_chain[1].id),
            error_messsage="Busy lock detected",
            barrier=threading.Barrier(1, timeout=3),
        )
