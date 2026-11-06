from monitor import attention
from monitor.constants import ALERT_RECLAIM_BYTES

GB = 1024 * 1024 * 1024


def advice(available=True, urgency=None, targets=None):
    return {
        "available": available,
        "summary": "s",
        "notes": "",
        "targets": targets or [],
        "urgency": urgency,
        "written_at": "2026-11-06T10:00:00",
        "error": None,
    }


def groups(*pairs):
    return [
        {"name": name, "rss": rss, "killable": killable}
        for name, rss, killable in pairs
    ]


class TestReclaimableBytes:
    def test_sums_only_the_targeted_groups(self):
        g = groups(("a.exe", 2 * GB, True), ("b.exe", 1 * GB, True))
        assert attention.reclaimable_bytes(g, {"a.exe"}) == 2 * GB

    def test_blocked_groups_never_count(self):
        # a blocked group cannot be killed, so its RAM is not reclaimable
        g = groups(("lsass.exe", 2 * GB, False))
        assert attention.reclaimable_bytes(g, {"lsass.exe"}) == 0

    def test_no_targets_is_zero(self):
        assert attention.reclaimable_bytes(groups(("a.exe", GB, True)), set()) == 0

    def test_empty_groups_is_zero(self):
        assert attention.reclaimable_bytes([], {"a.exe"}) == 0


class TestShouldAlert:
    def test_quiet_machine_with_payoff_stays_silent(self):
        # the user's own case: 71 percent and 1.8 GB reclaimable
        fire, _ = attention.should_alert(advice(), {"ram_percent": 71.0}, 1.8 * GB)
        assert fire is False

    def test_pressure_without_payoff_stays_silent(self):
        fire, _ = attention.should_alert(advice(), {"ram_percent": 88.0}, 0.3 * GB)
        assert fire is False

    def test_pressure_with_payoff_fires(self):
        fire, reason = attention.should_alert(advice(), {"ram_percent": 88.0}, 1.8 * GB)
        assert fire is True
        assert "88" in reason and "1.8 GB" in reason

    def test_exactly_at_both_thresholds_fires(self):
        fire, _ = attention.should_alert(
            advice(), {"ram_percent": 85.0}, ALERT_RECLAIM_BYTES
        )
        assert fire is True

    def test_critical_pressure_fires_with_no_payoff(self):
        fire, reason = attention.should_alert(advice(), {"ram_percent": 95.0}, 0)
        assert fire is True
        assert "critical" in reason.lower()

    def test_critical_pressure_fires_with_no_advice_at_all(self):
        fire, _ = attention.should_alert(advice(available=False), {"ram_percent": 95.0}, 0)
        assert fire is True

    def test_claude_can_force_an_alert_on_a_quiet_machine(self):
        fire, reason = attention.should_alert(
            advice(urgency="high"), {"ram_percent": 40.0}, 0
        )
        assert fire is True
        assert "Claude" in reason

    def test_urgency_is_case_insensitive(self):
        fire, _ = attention.should_alert(advice(urgency="HIGH"), {"ram_percent": 40.0}, 0)
        assert fire is True

    def test_unknown_urgency_value_is_ignored(self):
        fire, _ = attention.should_alert(
            advice(urgency="medium"), {"ram_percent": 40.0}, 0
        )
        assert fire is False

    def test_no_advice_on_a_normal_machine_stays_silent(self):
        fire, _ = attention.should_alert(
            advice(available=False), {"ram_percent": 88.0}, 2 * GB
        )
        assert fire is False

    def test_missing_totals_do_not_crash(self):
        fire, _ = attention.should_alert(advice(), {}, 0)
        assert fire is False


class TestAlertState:
    """Fires on entering the state, not on every 2 second refresh."""

    def test_fires_once_on_entering(self):
        state = attention.AlertState(cooldown=900)
        assert state.update(True, "k1", now=0.0) is True
        assert state.update(True, "k1", now=2.0) is False
        assert state.update(True, "k1", now=100.0) is False

    def test_silent_while_inactive(self):
        state = attention.AlertState(cooldown=900)
        assert state.update(False, "k1", now=0.0) is False

    def test_refires_after_the_condition_clears_and_returns(self):
        state = attention.AlertState(cooldown=900)
        assert state.update(True, "k1", now=0.0) is True
        assert state.update(False, "k1", now=10.0) is False
        assert state.update(True, "k1", now=20.0) is True

    def test_refires_when_the_advice_changes(self):
        state = attention.AlertState(cooldown=900)
        assert state.update(True, "k1", now=0.0) is True
        assert state.update(True, "k2", now=4.0) is True

    def test_refires_after_the_cooldown_on_a_standing_condition(self):
        state = attention.AlertState(cooldown=900)
        assert state.update(True, "k1", now=0.0) is True
        assert state.update(True, "k1", now=899.0) is False
        assert state.update(True, "k1", now=900.0) is True

    def test_acknowledging_silences_the_current_condition(self):
        # focusing the window stops the flash and must not immediately refire
        state = attention.AlertState(cooldown=900)
        assert state.update(True, "k1", now=0.0) is True
        state.acknowledge(now=5.0)
        assert state.update(True, "k1", now=7.0) is False

    def test_acknowledging_does_not_block_a_later_new_advice(self):
        state = attention.AlertState(cooldown=900)
        state.update(True, "k1", now=0.0)
        state.acknowledge(now=5.0)
        assert state.update(True, "k2", now=7.0) is True


class TestAlertKey:
    def test_key_changes_with_the_advice_timestamp(self):
        a = advice()
        b = dict(advice(), written_at="2026-11-06T11:00:00")
        assert attention.alert_key(a) != attention.alert_key(b)

    def test_key_is_stable_for_the_same_advice(self):
        assert attention.alert_key(advice()) == attention.alert_key(advice())

    def test_missing_advice_still_yields_a_key(self):
        assert attention.alert_key(None)


class TestTitleFor:
    def test_plain_title_when_not_alerting(self):
        assert attention.title_for(False, 0) == "sysmon"

    def test_badge_names_the_reclaimable_amount(self):
        assert attention.title_for(True, int(1.8 * GB)) == "sysmon - 1.8 GB reclaimable"

    def test_alert_with_no_payoff_says_pressure(self):
        assert attention.title_for(True, 0) == "sysmon - memory pressure"


class TestExpiredAdviceCannotAlert:
    """A day-old file describes a machine that has moved on."""

    def test_expired_advice_does_not_fire_the_normal_threshold(self):
        fire, _ = attention.should_alert(
            advice(), {"ram_percent": 90.0}, 2 * GB, expired=True
        )
        assert fire is False

    def test_expired_advice_does_not_fire_even_marked_urgent(self):
        fire, _ = attention.should_alert(
            advice(urgency="high"), {"ram_percent": 40.0}, 2 * GB, expired=True
        )
        assert fire is False

    def test_expired_advice_still_allows_the_critical_ram_alert(self):
        # the machine being full is a live fact, not an advice claim
        fire, reason = attention.should_alert(
            advice(), {"ram_percent": 96.0}, 0, expired=True
        )
        assert fire is True
        assert "critical" in reason.lower()

    def test_fresh_advice_is_unaffected(self):
        fire, _ = attention.should_alert(
            advice(), {"ram_percent": 90.0}, 2 * GB, expired=False
        )
        assert fire is True

    def test_expired_defaults_to_false_for_existing_callers(self):
        fire, _ = attention.should_alert(advice(), {"ram_percent": 90.0}, 2 * GB)
        assert fire is True


class TestLocalSourceUsesAHigherBar:
    """A rule is less trustworthy than a judgement, so it clears more."""

    def test_local_does_not_fire_at_claudes_threshold(self):
        # 88 percent and 1.5 GB fires for Claude, not for a rule
        fire, _ = attention.should_alert(
            advice(), {"ram_percent": 88.0}, int(1.5 * GB), local=True
        )
        assert fire is False

    def test_local_fires_at_its_own_threshold(self):
        fire, reason = attention.should_alert(
            advice(), {"ram_percent": 91.0}, int(2.5 * GB), local=True
        )
        assert fire is True
        assert "built-in" in reason.lower()

    def test_local_needs_both_pressure_and_payoff(self):
        fire, _ = attention.should_alert(
            advice(), {"ram_percent": 91.0}, int(1.0 * GB), local=True
        )
        assert fire is False

    def test_critical_ram_still_fires_for_local(self):
        fire, reason = attention.should_alert(
            advice(), {"ram_percent": 95.0}, 0, local=True
        )
        assert fire is True
        assert "critical" in reason.lower()

    def test_local_cannot_force_an_alert_with_urgency(self):
        # build_local_advice never sets urgency, but defend it here too
        fire, _ = attention.should_alert(
            advice(urgency="high"), {"ram_percent": 40.0}, 0, local=True
        )
        assert fire is False

    def test_claude_path_is_unaffected(self):
        fire, _ = attention.should_alert(advice(), {"ram_percent": 88.0}, int(1.5 * GB))
        assert fire is True
